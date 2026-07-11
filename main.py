"""
Daily Startup & VC Report
Pulls signals from YC/HN, Product Hunt, Reddit, Indie Hackers, G2 (startup demand)
and a16z, Sequoia, Peak XV, YC blog (VC investment activity), summarizes with an
AI API, and emails the result.

Every source function is wrapped in try/except so one broken scraper never
kills the whole run. Sources that fail are simply noted as unavailable in
the final email instead of crashing the job.
"""

import os
import json
import smtplib
import traceback
from email.mime.text import MIMEText

import requests
import feedparser
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DailyReportBot/1.0; +https://github.com/)"
}

# ---------------------------------------------------------------------------
# STARTUP DEMAND SOURCES
# ---------------------------------------------------------------------------

def fetch_hn_top(limit=8):
    """Hacker News top stories via official free API."""
    ids = requests.get(
        "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=15
    ).json()[:limit]
    items = []
    for i in ids:
        item = requests.get(
            f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=15
        ).json()
        if item:
            items.append(f"- {item.get('title')} ({item.get('score', 0)} pts) "
                          f"https://news.ycombinator.com/item?id={item.get('id')}")
    return items


def fetch_product_hunt(limit=8):
    """Today's top Product Hunt posts via GraphQL API. Needs PRODUCTHUNT_TOKEN."""
    token = os.environ["PRODUCTHUNT_TOKEN"]
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

    if "errors" in resp:
        # PH returns a 200 with an "errors" array instead of raising an HTTP
        # error, so we surface it explicitly rather than hitting a NoneType crash.
        raise RuntimeError(f"Product Hunt API error: {resp['errors']}")
    if not resp.get("data"):
        raise RuntimeError(f"Product Hunt returned no data: {resp}")

    edges = resp["data"]["posts"]["edges"]
    return [f"- {e['node']['name']} — {e['node']['tagline']} "
            f"({e['node']['votesCount']} votes) {e['node']['url']}" for e in edges]


def fetch_reddit(subreddits=("startups", "Entrepreneur"), limit=5):
    """Top daily posts via Reddit's public RSS feed.
    GitHub Actions runner IPs are commonly blocked by Reddit's Cloudflare
    protection on the .json endpoint regardless of headers used — the RSS
    feed is less aggressively protected and works more reliably from
    datacenter IPs. Still no auth required."""
    results = []
    for sub in subreddits:
        feed = feedparser.parse(
            f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit={limit}"
        )
        if not feed.entries:
            raise RuntimeError(f"No entries returned for r/{sub} (possibly blocked)")
        for entry in feed.entries[:limit]:
            results.append(f"- [r/{sub}] {entry.title} {entry.link}")
    return results


def fetch_indie_hackers(limit=8):
    """Lightweight scrape of Indie Hackers front page. No public API — fragile."""
    resp = requests.get("https://www.indiehackers.com/", headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "lxml")
    links = soup.select("a[href*='/post/']")[:limit]
    seen, results = set(), []
    for a in links:
        title = a.get_text(strip=True)
        href = a.get("href")
        if title and href and href not in seen:
            seen.add(href)
            full_url = href if href.startswith("http") else f"https://www.indiehackers.com{href}"
            results.append(f"- {title} {full_url}")
    return results


def fetch_g2_trending(limit=8):
    """Lightweight scrape of G2's trending/new software page.
    G2 is heavily bot-protected — this may return empty or break without warning."""
    resp = requests.get(
        "https://www.g2.com/best-software-companies", headers=HEADERS, timeout=15
    )
    soup = BeautifulSoup(resp.text, "lxml")
    items = soup.select("a")[:limit * 3]  # broad grab, filtered below
    results = []
    for a in items:
        text = a.get_text(strip=True)
        if text and len(text) > 3 and len(results) < limit:
            results.append(f"- {text}")
    return results


# ---------------------------------------------------------------------------
# INDIA-SPECIFIC SOURCES
# ---------------------------------------------------------------------------

def fetch_startup_india(limit=6):
    """Startup India (Government of India flagship portal) blog/news.
    Tries the known RSS feed first; falls back to a light scrape of the
    blog listing page if the feed URL has moved (gov sites restructure
    occasionally, so this is treated as fragile like the other scrapers)."""
    feed = feedparser.parse("https://www.startupindia.gov.in/content/sih/en/rss.xml")
    if feed.entries:
        return [f"- {e.title} {e.link}" for e in feed.entries[:limit]]

    # RSS returned nothing — fall back to scraping the blog page
    resp = requests.get(
        "https://www.startupindia.gov.in/content/sih/en/blogs.html",
        headers=HEADERS, timeout=15,
    )
    soup = BeautifulSoup(resp.text, "lxml")
    links = soup.select("a[href*='/blogs/']")[:limit]
    seen, results = set(), []
    for a in links:
        title = a.get_text(strip=True)
        href = a.get("href")
        if title and href and href not in seen:
            seen.add(href)
            full_url = href if href.startswith("http") else f"https://www.startupindia.gov.in{href}"
            results.append(f"- {title} {full_url}")
    if not results:
        raise RuntimeError("Neither RSS nor scrape fallback returned results")
    return results


def fetch_india_reddit(subreddits=("IndiaStartups", "india", "developersIndia", "IndianStreetBets"), limit=5):
    """Top daily posts from India-focused subreddits via RSS (same reasoning
    as fetch_reddit — RSS is less bot-protected than the .json endpoint)."""
    results = []
    for sub in subreddits:
        feed = feedparser.parse(
            f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit={limit}"
        )
        if not feed.entries:
            raise RuntimeError(f"No entries returned for r/{sub} (possibly blocked)")
        for entry in feed.entries[:limit]:
            results.append(f"- [r/{sub}] {entry.title} {entry.link}")
    return results




def fetch_rss(url, limit=6):
    """Generic RSS fetcher — used for a16z and YC blog."""
    feed = feedparser.parse(url)
    return [f"- {e.title} {e.link}" for e in feed.entries[:limit]]


def fetch_sequoia(limit=8):
    """Lightweight scrape of Sequoia's articles page. No RSS — fragile."""
    resp = requests.get("https://www.sequoiacap.com/articles/", headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "lxml")
    links = soup.select("a[href*='/article/']")[:limit]
    seen, results = set(), []
    for a in links:
        title = a.get_text(strip=True)
        href = a.get("href")
        if title and href and href not in seen:
            seen.add(href)
            full_url = href if href.startswith("http") else f"https://www.sequoiacap.com{href}"
            results.append(f"- {title} {full_url}")
    return results


def fetch_peakxv(limit=8):
    """Lightweight scrape of Peak XV's insights/news page. No RSS — fragile."""
    resp = requests.get("https://www.peakxv.com/insights", headers=HEADERS, timeout=15)
    soup = BeautifulSoup(resp.text, "lxml")
    links = soup.select("a")[:limit * 3]
    seen, results = set(), []
    for a in links:
        title = a.get_text(strip=True)
        href = a.get("href")
        if title and href and len(title) > 8 and href not in seen and len(results) < limit:
            seen.add(href)
            full_url = href if href.startswith("http") else f"https://www.peakxv.com{href}"
            results.append(f"- {title} {full_url}")
    return results


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
        return f"[{name}] Unavailable today (source error: {type(e).__name__})."


# ---------------------------------------------------------------------------
# PROBLEM STATEMENT EXTRACTION (feeds both the email and Agent 2's artifact)
# ---------------------------------------------------------------------------

def _strip_json_fences(text):
    """Strip all variants of markdown code fences from an AI JSON response.
    Handles: ```json, ```JSON, ``` (bare), with or without trailing fence."""
    text = text.strip()
    if text.startswith("```"):
        # Drop the opening fence line (e.g. ```json or just ```)
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        else:
            text = text[3:]  # bare ``` with no newline
        # Drop trailing closing fence
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _call_gemini_json(prompt, api_key, max_tokens=1024):
    """POST to Gemini and return the stripped text response."""
    model = "gemini-2.5-flash"
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"content-type": "application/json"},
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        timeout=60,
    )
    resp.raise_for_status()
    raw = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
    print(f"[extract_problem_statements] Raw AI response (first 500 chars):\n{raw[:500]}")
    return _strip_json_fences(raw)


def extract_problem_statements(startup_raw, vc_raw, india_raw):
    """Ask Gemini for a structured JSON list of problem statements drawn from
    ALL sources (startup demand, VC signals, and India-specific data), scored
    on severity and need, and ranked. Used both to build the main email's
    Problem Statements section and as a hand-off artifact for Agent 2."""
    api_key = os.environ["AI_API_KEY"]

    SCHEMA = (
        '[{"rank": 1, "statement": "...", "evidence": "one line", '
        '"domain": "e.g. fintech", "severity": 8, "need": 7, "priority_score": 15}]'
    )

    prompt = f"""Read all the raw data below and identify 3-5 clear, concrete
PROBLEM STATEMENTS — real unmet needs or recurring frustrations visible in
today's startup, VC, and tech community data. State each as a problem, not
a solution. Draw from any source — HN, Product Hunt, Reddit, VC blogs,
Indie Hackers, India-specific feeds — wherever the strongest signal is.

For each, score:
- severity (1-10): how painful the problem is if left unsolved
- need (1-10): breadth and urgency of demand based on today's evidence
- priority_score: severity + need (max 20)

Sort descending by priority_score. Return only the top 3-5.
Respond ONLY with valid JSON, no markdown fences, no commentary:
{SCHEMA}

=== STARTUP DEMAND RAW DATA ===
{startup_raw}

=== VC INVESTMENT RAW DATA ===
{vc_raw}

=== ADDITIONAL SIGNALS RAW DATA ===
{india_raw}
"""

    text = _call_gemini_json(prompt, api_key)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"[extract_problem_statements] JSON parse error: {e}\nRaw text: {text}")
        parsed = []

    if not parsed:
        print("[extract_problem_statements] Extraction returned empty — no problem statements today.")
        return []

    # Defensive re-sort and re-number
    parsed.sort(key=lambda p: p.get("priority_score", 0), reverse=True)
    for i, p in enumerate(parsed, start=1):
        p["rank"] = i
    print(f"[extract_problem_statements] Extracted {len(parsed)} problem statement(s).")
    return parsed


# ---------------------------------------------------------------------------
# AI SUMMARIZATION
# ---------------------------------------------------------------------------

def summarize_with_ai(startup_raw, vc_raw, india_raw, problem_statements):
    """Ask Gemini for the 6 content sections as a JSON object.
    No HTML in the prompt — formatting is handled entirely by build_email_html()."""
    ps_lines = "\n".join(
        f"  Rank #{p['rank']}: {p['statement']} "
        f"| Domain: {p['domain']} | Evidence: {p['evidence']} "
        f"| Severity: {p['severity']}/10 | Need: {p['need']}/10 "
        f"| Priority: {p['priority_score']}/20"
        for p in problem_statements
    ) or "  No problem statements extracted today."

    prompt = f"""You are a sharp startup analyst. Using the raw data below, write
content for SIX sections of a daily briefing. Return ONLY a valid JSON object
(no markdown fences, no commentary) with exactly these six keys:

{{
  "booming": "...",
  "demand": "...",
  "vc": "...",
  "problems": "...",
  "india": "...",
  "ideas": "..."
}}

Guidelines per key (each value is a short HTML fragment — only <p>, <ul>, <li>,
<strong>, <a href="URL"> tags; no wrapper divs, no inline styles):

"booming": 2-4 trends with real cross-source momentum. Synthesize — don't list.
"demand":  3-5 notable items, each as a <li> with a one-liner and a source link.
"vc":      3-5 VC items, same <li> format with links.
"problems": Use EXACTLY the ranked problem statements below — do not invent or
            re-score. Present each as a <li> with rank, statement, evidence, scores.
{ps_lines}
"india":   2-4 India-specific items as <li> with links. If data is thin, say so.
"ideas":   3 concrete startup ideas as <li>. Each: idea name in <strong>,
           then 1-2 sentences on who it's for and why the timing is right.

Tone: direct, opinionated analyst. No filler. No greetings. Under 600 words total.

=== STARTUP DEMAND RAW DATA ===
{startup_raw}

=== VC INVESTMENT RAW DATA ===
{vc_raw}

=== ADDITIONAL SIGNALS RAW DATA ===
{india_raw}
"""

    api_key = os.environ["AI_API_KEY"]
    model = "gemini-2.5-flash"
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"content-type": "application/json"},
        params={"key": api_key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 4096},
        },
        timeout=60,
    )
    resp.raise_for_status()
    candidate = resp.json()["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        print("WARNING: Gemini summarization truncated (MAX_TOKENS).")
    raw = candidate["content"]["parts"][0]["text"]
    print(f"[summarize_with_ai] Raw response (first 300 chars): {raw[:300]}")
    return _strip_json_fences(raw)


# ---------------------------------------------------------------------------
# HTML TEMPLATE — built entirely in Python, AI only fills the content
# ---------------------------------------------------------------------------

def build_email_html(sections: dict, problem_statements: list) -> str:
    """Render a fully styled email from the AI's section content dict."""
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
        """Wrap raw <li> fragments in a styled <ul>."""
        if "<li" in inner_html and "<ul" not in inner_html:
            return (
                '<ul style="margin:0;padding:0 0 0 18px;">'
                + inner_html.replace("<li", '<li style="margin-bottom:10px;color:#374151;"')
                + "</ul>"
            )
        return inner_html

    # Problem statements are rendered in Python for guaranteed badge styling
    def render_ps():
        if not problem_statements:
            return '<p style="color:#6b7280;font-size:13px;">No problem statements extracted today.</p>'
        parts = []
        for p in problem_statements:
            parts.append(
                f'<div style="border-left:4px solid #ef4444;padding:10px 14px;'
                f'margin-bottom:12px;background:#fef2f2;border-radius:0 6px 6px 0;">'
                f'<p style="margin:0 0 4px 0;font-weight:700;font-size:14px;color:#111827;">'
                f'#{p["rank"]} &mdash; {p["statement"]}</p>'
                f'<p style="margin:0 0 8px 0;font-size:12px;color:#6b7280;">'
                f'{p.get("evidence", "")} &middot; {p.get("domain", "")}</p>'
                f'<span style="display:inline-block;background:#fecaca;color:#991b1b;'
                f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;margin-right:4px;">'
                f'Severity {p["severity"]}/10</span>'
                f'<span style="display:inline-block;background:#dbeafe;color:#1d4ed8;'
                f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;margin-right:4px;">'
                f'Need {p["need"]}/10</span>'
                f'<span style="display:inline-block;background:#d1fae5;color:#065f46;'
                f'font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;">'
                f'Priority {p["priority_score"]}/20</span>'
                f'</div>'
            )
        return "".join(parts)

    html = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;'
        'color:#1f2937;max-width:640px;margin:0 auto;background:#f3f4f6;padding-bottom:32px;">'

        # Header banner
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

        # Footer
        '<div style="margin:8px 20px 0;padding:14px 20px;text-align:center;'
        'font-size:11px;color:#9ca3af;border-top:1px solid #e5e7eb;background:#ffffff;'
        'border-radius:0 0 8px 8px;">'
        'Automated daily briefing &mdash; HN &middot; Product Hunt &middot; Reddit '
        '&middot; Indie Hackers &middot; a16z &middot; YC &middot; Sequoia &middot; Inc42 &amp; more'
        '</div>'

        '</div>'
    )
    return html

def send_email(html_body: str):
    msg = MIMEText(html_body, "html")
    msg["Subject"] = "Your Morning Startup & VC Briefing"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["TO_EMAIL"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        server.send_message(msg)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    startup_sections = [
        safe_fetch("Hacker News / YC", fetch_hn_top),
        safe_fetch("Product Hunt", fetch_product_hunt),
        safe_fetch("Reddit", fetch_reddit),
        safe_fetch("Indie Hackers", fetch_indie_hackers),
        safe_fetch("G2", fetch_g2_trending),
    ]

    vc_sections = [
        safe_fetch("a16z", fetch_rss, "https://a16z.com/feed/"),
        safe_fetch("YC Blog", fetch_rss, "https://www.ycombinator.com/blog/rss/"),
        safe_fetch("Sequoia", fetch_sequoia),
        safe_fetch("Peak XV", fetch_peakxv),
    ]

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
        problem_statements = extract_problem_statements(startup_raw, vc_raw, india_raw)
    except Exception as e:
        print(f"Problem statement extraction failed: {e}")
        traceback.print_exc()
        problem_statements = []

    # Save as a hand-off artifact for Agent 2, which runs after this job and
    # searches adjacent domains for related problem statements.
    with open("problem_statements.json", "w") as f:
        json.dump(problem_statements, f, indent=2)

    try:
        raw_json = summarize_with_ai(startup_raw, vc_raw, india_raw, problem_statements)
        sections = json.loads(raw_json)
        html_body = build_email_html(sections, problem_statements)
    except Exception as e:
        print(f"AI summarization failed: {e}")
        traceback.print_exc()
        # Fallback: still send a styled email using only the extracted PS
        html_body = build_email_html({}, problem_statements)

    send_email(html_body)
    print("Report sent successfully.")


if __name__ == "__main__":
    main()
