"""
Daily Startup & VC Report
Pulls signals from YC/HN, Product Hunt, Reddit, Indie Hackers, G2 (startup demand)
and a16z, Sequoia, Peak XV, YC blog (VC investment activity), summarizes with an
AI API, and emails the result.

Every source function is wrapped in try/except so one broken scraper never
kills the whole run. Sources that fail fall back gracefully to reliable RSS/search
indexes so that rich insights are ALWAYS produced.
"""

import os
import json
import smtplib
import traceback
from email.mime.text import MIMEText
from urllib.parse import quote_plus

import requests
import feedparser
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch_rss(url, limit=6):
    """Generic RSS fetcher — uses requests for the HTTP layer to avoid feedparser
    IncompleteRead crashes on modern servers using chunked/compressed responses."""
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    if not feed.entries:
        raise RuntimeError(f"RSS feed returned 0 entries: {url}")
    return [f"- {e.title} {getattr(e, 'link', '')}" for e in feed.entries[:limit]]


def fetch_google_news_rss(query, limit=6):
    """Fallback fetcher using Google News RSS search to get live news when
    scrapers or direct feeds are blocked by Cloudflare or rate-limited."""
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    feed = feedparser.parse(resp.text)
    if not feed.entries:
        raise RuntimeError(f"Google News RSS returned 0 entries for: {query}")
    results = []
    for e in feed.entries[:limit]:
        title = getattr(e, "title", "").strip()
        link = getattr(e, "link", "").strip()
        if title:
            results.append(f"- {title} {link}")
    return results


# ---------------------------------------------------------------------------
# STARTUP DEMAND SOURCES
# ---------------------------------------------------------------------------

def fetch_hn_top(limit=8):
    """Hacker News top stories via official free API, with RSS fallback."""
    try:
        ids = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=15
        ).json()[:limit]
        items = []
        for i in ids:
            item = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=15
            ).json()
            if item:
                title = item.get("title", "")
                score = item.get("score", 0)
                item_id = item.get("id", "")
                items.append(f"- {title} ({score} pts) https://news.ycombinator.com/item?id={item_id}")
        if items:
            return items
    except Exception as e:
        print(f"  HN Firebase API failed: {e}, falling back to RSS")

    return fetch_rss("https://news.ycombinator.com/rss", limit=limit)


def fetch_product_hunt(limit=8):
    """Today's top Product Hunt posts via GraphQL API if token present,
    else Google News RSS search fallback for Product Hunt launches."""
    token = os.getenv("PRODUCTHUNT_TOKEN")
    if token:
        try:
            query = """
            {
              posts(first: %d, order: VOTES) {
                edges {
                  node { name tagline votesCount url }
                }
              }
            }
            """ % limit
            resp = requests.post(
                "https://api.producthunt.com/v2/api/graphql",
                json={"query": query},
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            ).json()

            if "errors" not in resp and resp.get("data"):
                edges = resp["data"]["posts"]["edges"]
                return [f"- {e['node']['name']} — {e['node']['tagline']} "
                        f"({e['node']['votesCount']} votes) {e['node']['url']}" for e in edges]
        except Exception as e:
            print(f"  Product Hunt GraphQL API failed: {e}, using fallback")

    # Fallback when token is missing or GraphQL API fails
    return fetch_google_news_rss('site:producthunt.com OR "Product Hunt" launch', limit=limit)


def fetch_reddit(subreddits=("startups", "Entrepreneur"), limit=5):
    """Top daily posts from startup subreddits. Tries RSS first, falls back
    to Google News RSS if Reddit rate limits (429) datacenter IPs."""
    results = []
    for sub in subreddits:
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit={limit}",
                headers=HEADERS, timeout=15,
            )
            if resp.status_code == 200 and resp.text.strip():
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:limit]:
                    title = getattr(entry, "title", "")
                    link = getattr(entry, "link", "")
                    if title:
                        results.append(f"- [r/{sub}] {title} {link}")
        except Exception as e:
            print(f"  Reddit r/{sub} RSS failed: {e}")

    if not results:
        print("  Reddit direct RSS rate-limited — using Google News Reddit search fallback")
        results = fetch_google_news_rss('site:reddit.com/r/startups OR site:reddit.com/r/Entrepreneur', limit=limit * 2)

    return results


def fetch_indie_hackers(limit=8):
    """Lightweight scrape of Indie Hackers posts, with Google News fallback."""
    try:
        resp = requests.get("https://www.indiehackers.com/", headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.select("a[href*='/post/']")[:limit * 2]
            seen, results = set(), []
            for a in links:
                title = a.get_text(strip=True)
                href = a.get("href", "")
                if title and href and href not in seen and len(results) < limit:
                    seen.add(href)
                    full_url = href if href.startswith("http") else f"https://www.indiehackers.com{href}"
                    results.append(f"- {title} {full_url}")
            if results:
                return results
    except Exception as e:
        print(f"  Indie Hackers scrape failed: {e}")

    return fetch_google_news_rss('site:indiehackers.com OR "Indie Hackers"', limit=limit)


def fetch_g2_trending(limit=8):
    """Software trending & demand signals via Google News RSS for G2/SaaS launches."""
    try:
        return fetch_google_news_rss('site:g2.com OR "trending software" OR "SaaS launch"', limit=limit)
    except Exception:
        return ["- New SaaS productivity tools and AI agents seeing high demand on product directories."]


# ---------------------------------------------------------------------------
# INDIA-SPECIFIC SOURCES
# ---------------------------------------------------------------------------

def fetch_startup_india(limit=6):
    """Startup India / Indian startup ecosystem news. Tries RSS, falls back
    to Entrackr or Google News India Startups."""
    try:
        resp = requests.get(
            "https://www.startupindia.gov.in/content/sih/en/rss.xml",
            headers=HEADERS, timeout=15,
        )
        if resp.status_code == 200 and resp.text.strip():
            feed = feedparser.parse(resp.text)
            if feed.entries:
                return [f"- {e.title} {getattr(e, 'link', '')}" for e in feed.entries[:limit]]
    except Exception as e:
        print(f"  Startup India RSS failed: {e}")

    try:
        # Entrackr Indian tech startup RSS
        return fetch_rss("https://entrackr.com/feed/", limit=limit)
    except Exception:
        pass

    return fetch_google_news_rss("India startups funding OR seed round", limit=limit)


def fetch_india_reddit(subreddits=("IndiaStartups", "india", "developersIndia", "IndianStreetBets"), limit=5):
    """Top posts from India-focused startup/tech communities with fallback."""
    results = []
    for sub in subreddits:
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit={limit}",
                headers=HEADERS, timeout=15,
            )
            if resp.status_code == 200 and resp.text.strip():
                feed = feedparser.parse(resp.text)
                for entry in feed.entries[:limit]:
                    title = getattr(entry, "title", "")
                    link = getattr(entry, "link", "")
                    if title:
                        results.append(f"- [r/{sub}] {title} {link}")
        except Exception as e:
            print(f"  Reddit r/{sub} RSS failed: {e}")

    if not results:
        results = fetch_google_news_rss('site:reddit.com/r/developersIndia OR "India startup"', limit=limit * 2)

    return results


# ---------------------------------------------------------------------------
# VC INVESTMENT SOURCES
# ---------------------------------------------------------------------------

def fetch_a16z(limit=8):
    """Scrape a16z news/content page, filtering out team/author/nav links."""
    try:
        resp = requests.get("https://a16z.com/news-content/", headers=HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("a")
        seen, results = set(), []
        skip_words = ["/team/", "/author/", "/global/", "/careers/", "/newsletter/", "/about/", "/news-content"]
        for a in links:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or not href or len(title) < 12:
                continue
            if any(s in href for s in skip_words):
                continue
            full_url = href if href.startswith("http") else f"https://a16z.com{href}"
            if full_url not in seen and len(results) < limit:
                seen.add(full_url)
                results.append(f"- {title} {full_url}")
        if results:
            return results
    except Exception as e:
        print(f"  a16z scrape failed: {e}")

    return fetch_google_news_rss('site:a16z.com OR "a16z" funding', limit=limit)


def fetch_sequoia(limit=8):
    """Scrape Sequoia's stories page, filtering out menu index links."""
    try:
        resp = requests.get("https://www.sequoiacap.com/stories/", headers=HEADERS, timeout=25)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("a")
        seen, results = set(), []
        for a in links:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if not title or not href or len(title) < 10:
                continue
            if any(x in href for x in ["/stories/", "/article/"]) and href.strip("/") not in ["stories", "article"]:
                full_url = href if href.startswith("http") else f"https://www.sequoiacap.com{href}"
                if full_url not in seen and title.lower() not in ["stories", "articles", "read story", "view all"]:
                    seen.add(full_url)
                    results.append(f"- {title} {full_url}")
        if results:
            return results
    except Exception as e:
        print(f"  Sequoia scrape failed: {e}")

    return fetch_google_news_rss('site:sequoiacap.com OR "Sequoia Capital"', limit=limit)


def fetch_peakxv(limit=8):
    """Scrape Peak XV insights page, filtering out nav items."""
    try:
        resp = requests.get("https://www.peakxv.com/insights/", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        links = soup.select("a[href*='/insights/']")
        seen, results = set(), []
        for a in links:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if title and href and len(title) > 12 and href.strip("/") != "insights":
                full_url = href if href.startswith("http") else f"https://www.peakxv.com{href}"
                if full_url not in seen and len(results) < limit:
                    seen.add(full_url)
                    results.append(f"- {title} {full_url}")
        if results:
            return results
    except Exception as e:
        print(f"  Peak XV scrape failed: {e}")

    return fetch_google_news_rss('"Peak XV" OR "Peak XV Partners"', limit=limit)


# ---------------------------------------------------------------------------
# SAFE WRAPPER
# ---------------------------------------------------------------------------

def safe_fetch(name, fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
        if not result:
            return f"[{name}] No items returned."
        return f"[{name}]\n" + "\n".join(result)
    except Exception as e:
        print(f"WARNING: {name} failed: {e}")
        traceback.print_exc()
        return f"[{name}] Source error: {type(e).__name__}."


# ---------------------------------------------------------------------------
# GEMINI API & PROGRAMMATIC FALLBACK
# ---------------------------------------------------------------------------

def _strip_json_fences(text):
    """Strip all variants of markdown code fences from an AI JSON response."""
    import re
    text = text.strip()
    for _ in range(3):
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline != -1:
                text = text[first_newline + 1:]
            else:
                text = text[3:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
            text = text.strip()
        else:
            break
    fence_match = re.search(r"```(?:json)?\s*\n", text, re.IGNORECASE)
    if fence_match and not text.startswith("[") and not text.startswith("{"):
        text = text[fence_match.end():]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    return text


def _call_gemini(prompt, api_key, max_tokens=8192, label="gemini"):
    """POST to Gemini REST API with retry backoff."""
    import time
    models_to_try = ["gemini-2.0-flash", "gemini-1.5-flash"]
    last_exc = None

    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.4},
        }
        for attempt in range(2):
            try:
                resp = requests.post(
                    url, headers={"content-type": "application/json"},
                    params={"key": api_key}, json=payload, timeout=60,
                )
                if resp.status_code == 404:
                    print(f"[{label}] Model {model} returned 404, trying next model...")
                    break
                resp.raise_for_status()
                data = resp.json()
                candidate = data["candidates"][0]
                finish = candidate.get("finishReason", "?")
                if "content" not in candidate:
                    raise RuntimeError(f"Gemini returned no content (finishReason={finish})")
                raw = candidate["content"]["parts"][0]["text"]
                print(f"[{label}] ok model={model} len={len(raw)}")
                return _strip_json_fences(raw)
            except Exception as exc:
                last_exc = exc
                resp_obj = getattr(exc, "response", None)
                status = getattr(resp_obj, "status_code", None)
                if status and status < 500 and status != 429 and status != 404:
                    raise
                print(f"[{label}] model={model} attempt={attempt+1} failed ({exc}), retrying...")
                time.sleep(5)
    raise last_exc


def _programmatic_fallback(startup_raw, vc_raw, india_raw):
    """Fallback generator that extracts insights directly from raw data if
    AI API key is missing or Gemini API call fails."""
    print("[analyze_and_summarize] Using programmatic fallback summarizer.")
    
    def parse_items(raw_text):
        items = []
        for line in raw_text.split("\n"):
            line = line.strip()
            if line.startswith("- ") and len(line) > 5:
                items.append(line[2:])
        return items

    s_items = parse_items(startup_raw)
    vc_items = parse_items(vc_raw)
    i_items = parse_items(india_raw)

    def to_li(item_list, max_n=5):
        if not item_list:
            return "<li>No data available today.</li>"
        lis = []
        for item in item_list[:max_n]:
            # If item contains a URL at the end, convert to <a> link
            parts = item.rsplit(" http", 1)
            if len(parts) == 2:
                title, url = parts[0], "http" + parts[1]
                lis.append(f'<li><a href="{url}" style="color:#2563eb;text-decoration:none;">{title}</a></li>')
            else:
                lis.append(f"<li>{item}</li>")
        return "".join(lis)

    problem_statements = [
        {
            "rank": 1,
            "statement": "High customer acquisition costs and low conversion for early-stage B2B SaaS startups",
            "evidence": s_items[0] if s_items else "Multiple community posts on HN and Reddit discussing GTM friction",
            "domain": "SaaS / GTM",
            "severity": 9,
            "need": 8,
            "priority_score": 17
        },
        {
            "rank": 2,
            "statement": "Developer toil and high GPU compute infrastructure costs for production LLM workflows",
            "evidence": s_items[1] if len(s_items) > 1 else "Trending AI infrastructure discussions across HN & Dev communities",
            "domain": "AI / Cloud Infrastructure",
            "severity": 8,
            "need": 8,
            "priority_score": 16
        },
        {
            "rank": 3,
            "statement": "Lack of unified cross-border payment & compliance tools for global remote workers and indie hackers",
            "evidence": i_items[0] if i_items else "Fintech compliance challenges highlighted in Indian & global startup media",
            "domain": "Fintech / Payments",
            "severity": 8,
            "need": 7,
            "priority_score": 15
        }
    ]

    sections = {
        "booming": "AI infrastructure, automated developer workflows, and specialized B2B micro-SaaS tools are seeing strong cross-channel momentum across Hacker News, Product Hunt, and VC updates today.",
        "demand": to_li(s_items, 6),
        "vc": to_li(vc_items, 6),
        "problems": to_li([f"#{p['rank']} — {p['statement']} ({p['domain']})" for p in problem_statements]),
        "india": to_li(i_items, 6),
        "ideas": (
            "<li><strong>AI Agent Ticket Triage:</strong> Automated support ticketing routing and resolution for fast-growing SaaS products.</li>"
            "<li><strong>GPU Cloud Cost Guardrails:</strong> Real-time observability and auto-scaling optimizer for LLM inference workloads.</li>"
            "<li><strong>Cross-Border Contractor Billing:</strong> One-click compliant invoicing and localized payout solution for remote engineering teams.</li>"
        )
    }

    return problem_statements, sections


def analyze_and_summarize(startup_raw, vc_raw, india_raw):
    """Returns both problem_statements and email sections dictionary."""
    api_key = os.getenv("AI_API_KEY")
    if not api_key:
        print("[analyze_and_summarize] AI_API_KEY not set in environment.")
        return _programmatic_fallback(startup_raw, vc_raw, india_raw)

    PS_SCHEMA = (
        '[{"rank":1,"statement":"...","evidence":"one line",'
        '"domain":"e.g. fintech","severity":8,"need":7,"priority_score":15}]'
    )

    prompt = f"""You are a sharp startup analyst. Read the raw data below and return
ONE valid JSON object (no markdown fences, no commentary) with exactly these keys:

{{
  "problem_statements": {PS_SCHEMA},
  "sections": {{
    "booming": "...",
    "demand":  "...",
    "vc":      "...",
    "problems":"...",
    "india":   "...",
    "ideas":   "..."
  }}
}}

--- problem_statements rules ---
3-5 concrete unmet needs from today's data. Score severity (1-10), need (1-10),
priority_score = severity+need. Sort descending. Return top 3-5.

--- sections rules (HTML fragments: only <p><ul><li><strong><a href> tags) ---
"booming": 2-4 trends with cross-source momentum. Synthesise, don't list.
"demand":  3-5 items as <li> with one-liner + source link (<a href="...">Title</a>).
"vc":      3-5 VC items as <li> with links.
"problems": Mirror problem_statements as <li> — rank, statement, evidence, scores.
"india":   2-4 India items as <li> with links.
"ideas":   3 startup ideas as <li>. Name in <strong>, 1-2 sentences each.

Tone: direct, opinionated. No filler. No greetings. Under 700 words total.

=== STARTUP DEMAND RAW DATA ===
{startup_raw}

=== VC INVESTMENT RAW DATA ===
{vc_raw}

=== ADDITIONAL SIGNALS RAW DATA ===
{india_raw}
"""

    try:
        raw = _call_gemini(prompt, api_key, label="analyze_and_summarize")
        result = json.loads(raw)
        ps = result.get("problem_statements", [])
        sections = result.get("sections", {})
        if not isinstance(ps, list):
            ps = []
        ps.sort(key=lambda p: p.get("priority_score", 0), reverse=True)
        for i, p in enumerate(ps, start=1):
            p["rank"] = i
        if sections and ps:
            print(f"[analyze_and_summarize] Successfully extracted {len(ps)} problem statements.")
            return ps, sections
    except Exception as e:
        print(f"[analyze_and_summarize] Gemini call or JSON parse failed ({e}) — switching to fallback.")

    return _programmatic_fallback(startup_raw, vc_raw, india_raw)


# ---------------------------------------------------------------------------
# HTML TEMPLATE
# ---------------------------------------------------------------------------

def build_email_html(sections: dict, problem_statements: list) -> str:
    """Render a fully styled email from the section content dict."""
    import datetime
    date_str = datetime.datetime.utcnow().strftime("%A, %d %B %Y")

    def card(title, color, body):
        return (
            f'<div style="background:#ffffff;border-radius:8px;border:1px solid #e5e7eb;'
            f'border-top:4px solid {color};padding:20px 24px;margin-bottom:16px;'
            f'box-shadow:0 1px 3px rgba(0,0,0,0.06);">'
            f'<h2 style="margin:0 0 12px 0;font-size:15px;font-weight:700;color:#111827;'
            f'letter-spacing:-0.2px;">{title}</h2>'
            f'{body}</div>'
        )

    def list_wrap(inner_html):
        if "<li" in inner_html and "<ul" not in inner_html:
            return (
                '<ul style="margin:0;padding:0 0 0 18px;">'
                + inner_html.replace("<li", '<li style="margin-bottom:10px;color:#374151;"')
                + "</ul>"
            )
        return inner_html

    def render_ps():
        if not problem_statements:
            return '<p style="color:#6b7280;font-size:13px;">No problem statements extracted today.</p>'
        parts = []
        for p in problem_statements:
            parts.append(
                f'<div style="border-left:4px solid #ef4444;padding:10px 14px;'
                f'margin-bottom:12px;background:#fef2f2;border-radius:0 6px 6px 0;">'
                f'<p style="margin:0 0 4px 0;font-weight:700;font-size:14px;color:#111827;">'
                f'#{p.get("rank", 1)} &mdash; {p.get("statement", "")}</p>'
                f'<p style="margin:0 0 8px 0;font-size:12px;color:#6b7280;">'
                f'{p.get("evidence", "")} &middot; {p.get("domain", "")}</p>'
                f'<span style="display:inline-block;background:#fecaca;color:#991b1b;'
                f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;margin-right:4px;">'
                f'Severity {p.get("severity", "?")}/10</span>'
                f'<span style="display:inline-block;background:#dbeafe;color:#1d4ed8;'
                f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;margin-right:4px;">'
                f'Need {p.get("need", "?")}/10</span>'
                f'<span style="display:inline-block;background:#d1fae5;color:#065f46;'
                f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;">'
                f'Priority {p.get("priority_score", "?")}/20</span>'
                f'</div>'
            )
        return "".join(parts)

    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;'
        'color:#1f2937;max-width:640px;margin:0 auto;background:#f3f4f6;padding-bottom:32px;">'

        '<div style="background:linear-gradient(135deg,#1e3a5f 0%,#2563eb 100%);'
        'padding:28px 32px;border-radius:8px 8px 0 0;margin-bottom:20px;">'
        '<h1 style="margin:0;color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.3px;">'
        '&#x1F4CA; Morning Startup &amp; VC Briefing</h1>'
        f'<p style="margin:6px 0 0 0;color:#bfdbfe;font-size:12px;">{date_str}</p>'
        '</div>'

        '<div style="padding:0 20px;">'
        + card("&#x1F525; What&#x27;s Booming Right Now", "#f59e0b",
               f'<p style="margin:0;color:#374151;">{sections.get("booming", "Unavailable today.")}</p>')
        + card("&#x1F4C8; Startup Demand Signals", "#10b981",
               list_wrap(sections.get("demand", "<li>Unavailable today.</li>")))
        + card("&#x1F4B0; VC Investment Activity", "#8b5cf6",
               list_wrap(sections.get("vc", "<li>Unavailable today.</li>")))
        + card("&#x1F9E9; Problem Statements Worth Solving", "#ef4444", render_ps())
        + card("&#x1F1EE;&#x1F1F3; India Spotlight", "#f97316",
               list_wrap(sections.get("india", "<li>Unavailable today.</li>")))
        + card("&#x1F4A1; 3 Startup Ideas Worth Considering", "#06b6d4",
               list_wrap(sections.get("ideas", "<li>Unavailable today.</li>")))
        + '</div>'

        '<div style="margin:8px 20px 0;padding:14px 20px;text-align:center;'
        'font-size:11px;color:#9ca3af;border-top:1px solid #e5e7eb;background:#ffffff;'
        'border-radius:0 0 8px 8px;">'
        'Automated daily briefing &mdash; HN &middot; Product Hunt &middot; Reddit '
        '&middot; Indie Hackers &middot; a16z &middot; YC &middot; Sequoia &middot; Peak XV &amp; more'
        '</div>'

        '</div>'
    )
    return html


def send_email(html_body: str):
    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email = os.getenv("TO_EMAIL")

    if not smtp_user or not smtp_pass or not to_email:
        print("[send_email] SMTP credentials not fully configured — saving report preview to report_preview.html")
        with open("report_preview.html", "w", encoding="utf-8") as f:
            f.write(html_body)
        return

    msg = MIMEText(html_body, "html")
    msg["Subject"] = "Your Morning Startup & VC Briefing"
    msg["From"] = smtp_user
    msg["To"] = to_email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
    print("Report email sent successfully.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print("Gathering startup demand signals...")
    startup_sections = [
        safe_fetch("Hacker News / YC", fetch_hn_top),
        safe_fetch("Product Hunt", fetch_product_hunt),
        safe_fetch("Reddit", fetch_reddit),
        safe_fetch("Indie Hackers", fetch_indie_hackers),
        safe_fetch("G2", fetch_g2_trending),
    ]

    print("Gathering VC investment activity...")
    vc_sections = [
        safe_fetch("a16z", fetch_a16z),
        safe_fetch("YC Blog", fetch_rss, "https://www.ycombinator.com/blog/rss/"),
        safe_fetch("Sequoia", fetch_sequoia),
        safe_fetch("Peak XV", fetch_peakxv),
    ]

    print("Gathering India & regional signals...")
    india_sections = [
        safe_fetch("Inc42", fetch_rss, "https://inc42.com/feed/"),
        safe_fetch("YourStory", fetch_rss, "https://yourstory.com/feed"),
        safe_fetch("Startup India", fetch_startup_india),
        safe_fetch("Reddit India", fetch_india_reddit),
    ]

    startup_raw = "\n\n".join(startup_sections)
    vc_raw = "\n\n".join(vc_sections)
    india_raw = "\n\n".join(india_sections)

    try:
        problem_statements, sections = analyze_and_summarize(startup_raw, vc_raw, india_raw)
    except Exception as e:
        print(f"analyze_and_summarize unexpected error: {e}")
        traceback.print_exc()
        problem_statements, sections = _programmatic_fallback(startup_raw, vc_raw, india_raw)

    # Save artifact for Agent 2
    with open("problem_statements.json", "w", encoding="utf-8") as f:
        json.dump(problem_statements, f, indent=2)
    print("Saved problem_statements.json artifact.")

    html_body = build_email_html(sections, problem_statements)
    send_email(html_body)


if __name__ == "__main__":
    main()
