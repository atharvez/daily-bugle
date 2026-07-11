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
    """Ask Gemini for a clean, structured JSON list of INDIA-CENTRIC problem
    statements, each scored on severity and need, and ranked. Used both to
    build the main email's Problem Statements section and as a hand-off
    artifact for Agent 2, which searches adjacent domains for related/
    similar problem statements.

    Two-pass strategy:
      Pass 1: India-centric sources preferred.
      Pass 2 (fallback): If pass 1 returns [], relax constraints and draw
              from ALL available data so the email is never empty.
    """
    api_key = os.environ["AI_API_KEY"]

    SCHEMA = (
        '[{"rank": 1, "statement": "...", "evidence": "one line", '
        '"domain": "e.g. fintech", "severity": 8, "need": 7, "priority_score": 15}]'
    )

    # ---- Pass 1: India-centric ----
    prompt1 = f"""Read the raw data below and identify 2-3 clear, concrete
INDIA-CENTRIC PROBLEM STATEMENTS — real unmet needs or recurring frustrations
relevant to Indian consumers, businesses, or the Indian startup/VC ecosystem.
State each as a problem, not a solution.

Primary source: INDIA-SPECIFIC RAW DATA (Inc42, YourStory, r/IndiaStartups,
r/india). Also pull from the general data ONLY if clearly India-relevant.
If India data is thin, return fewer items — do not invent connections.

For each, score:
- severity (1-10): pain if unsolved
- need (1-10): breadth/urgency of demand from today's evidence
- priority_score: severity + need

Sort descending by priority_score.
Respond ONLY with valid JSON, no markdown fences, no commentary:
{SCHEMA}

=== STARTUP DEMAND RAW DATA ===
{startup_raw}

=== VC INVESTMENT RAW DATA ===
{vc_raw}

=== INDIA-SPECIFIC RAW DATA ===
{india_raw}
"""

    text1 = _call_gemini_json(prompt1, api_key)
    try:
        parsed = json.loads(text1)
    except json.JSONDecodeError as e:
        print(f"[extract_problem_statements] Pass 1 JSON parse error: {e}\nRaw text: {text1}")
        parsed = []

    # ---- Pass 2: Permissive fallback if Pass 1 returned nothing ----
    if not parsed:
        print("[extract_problem_statements] Pass 1 returned empty — running permissive fallback.")
        prompt2 = f"""The India-specific data sources may be sparse today.
Using ALL the raw data below, identify 2-3 problem statements that would
relevant to building a startup product — they don't need to be India-exclusive,
but prefer ones with an India angle if any exists.

For each, score severity, need, priority_score (same 1-10 scale).
Sort descending by priority_score.
Respond ONLY with valid JSON, no markdown fences:
{SCHEMA}

=== STARTUP DEMAND RAW DATA ===
{startup_raw}

=== VC INVESTMENT RAW DATA ===
{vc_raw}

=== INDIA-SPECIFIC RAW DATA ===
{india_raw}
"""
        text2 = _call_gemini_json(prompt2, api_key)
        try:
            parsed = json.loads(text2)
        except json.JSONDecodeError as e:
            print(f"[extract_problem_statements] Pass 2 JSON parse error: {e}\nRaw text: {text2}")
            parsed = []

    if not parsed:
        print("[extract_problem_statements] Both passes returned empty — no problem statements today.")
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
    problem_statements_text = "\n".join(
        f"- Rank #{p['rank']}: {p['statement']} "
        f"(Evidence: {p['evidence']}; Domain: {p['domain']}; "
        f"Severity: {p['severity']}/10; Need: {p['need']}/10; "
        f"Priority Score: {p['priority_score']}/20)"
        for p in problem_statements
    ) or "No problem statements were extracted today."
    prompt = f"""You are a sharp, well-read startup analyst writing a morning
briefing email. Tone should be natural and conversational, like a smart
person sharing genuinely interesting observations — not corporate, not
robotic, but also not addressed to any named person.

Write in a natural, conversational tone. Full sentences where they help,
short punchy bullets where a list is clearer. No generic filler phrases
like "in today's fast-paced world." Have an actual point of view — if
something looks like noise, say so; if something looks like a real signal,
say why.

Structure the email in SIX sections, in this order:

1. **What's Booming Right Now** — Look across all the raw data (HN, Product
   Hunt, Reddit, Indie Hackers, G2) and identify 2-4 categories/niches that
   show real momentum today (repeated themes, high engagement, multiple
   independent mentions). Don't just list posts — synthesize the pattern.
   E.g. "AI coding agents are having a moment again — three of today's top
   HN posts and two PH launches are in this space."

2. **Startup Demand Signals** — The 3-5 most notable individual items,
   grouped sensibly, each with a one-line "why this matters" instead of
   just a bare link. Include source links.

3. **VC Investment Activity** — Same treatment for the VC-side raw data:
   3-5 notable items, grouped, one line of context each, links included.

4. **India-Centric Problem Statements Worth Solving** — Present the
   problem statements listed below (already extracted, scored, and ranked,
   focused specifically on the Indian market) in rank order, each showing
   its rank, the statement, its evidence line, and its Severity/Need/
   Priority scores clearly (e.g. as a small inline badge or parenthetical
   like "Severity 8/10 · Need 7/10 · Priority 15/20"). Do not invent new
   ones or re-score them — use exactly what's provided:
   {problem_statements_text}

5. **India Spotlight** — Using the India-specific raw data, cover what's
   notable in the Indian startup/VC scene today: 2-4 items, grouped, one
   line of context each, links included. If the India data is thin or
   mostly unavailable, say so briefly rather than padding it out.

6. **3 Startup Ideas Worth Considering** — Based on the gaps, complaints,
   or unmet demand you can infer from today's data (including the India-
   centric problem statements above), propose 3 concrete, specific startup
   ideas — at least 1-2 of which should directly address the India-centric
   problem statements listed. Each should be 1-2 sentences: what it is,
   who it's for, and why today's data suggests the timing is right. Be
   opinionated and specific — avoid vague ideas like "an AI tool for X."

Keep the whole email under 550 words total — this is a hard limit, not a
suggestion. Being complete and finishing properly matters more than
covering every possible item. If you're running long, cut an item rather
than risk being cut off mid-sentence.

STRICT OUTPUT RULES — read carefully, these are not optional:
- Do NOT include a greeting, salutation, "Hey [Name]," sign-off, or any
  placeholder text like [Founder Friend's Name]. This is an automated
  email with no recipient name available — never invent one or leave a
  placeholder for one. Start directly with the content.
- Do NOT include any preamble, meta-commentary, or explanation of what
  you're about to do (e.g. no "Here's a rundown..." intro line).
- Output ONLY valid HTML. Never mix in plain, untagged prose sentences —
  every piece of visible text must be inside an HTML tag.
- Never use Markdown syntax (*, #, -, backticks) anywhere in the output.
- Never truncate mid-tag or mid-sentence — if you are running long, cut
  content (drop an item or shorten a section) rather than cutting off
  output partway through.
- The very first characters of your response must be exactly: <div
- The very last characters of your response must be exactly: </div>
- Nothing may appear before the opening <div> or after the closing </div>
  — no code fences, no commentary, nothing.

HTML STRUCTURE:
- Wrap everything in a single <div> with inline styles (no <html>/<head>/
  <body> tags, no external CSS, no classes — this goes straight into an
  email body).
- Use <h2 style="..."> for the six section titles.
- Use <p style="..."> for narrative paragraphs.
- Use <ul><li style="..."> for bullet lists.
- Use <a href="URL" style="color:#2563eb;"> for links, with real anchor
  text (never show a bare raw URL as visible text).
- Use <strong> for emphasis instead of markdown asterisks.
- Keep inline styles minimal and email-safe: font-family: Arial, sans-serif;
  font-size: 14px; line-height: 1.5; color: #1a1a1a; a bit of margin
  between sections.

=== STARTUP DEMAND RAW DATA ===
{startup_raw}

=== VC INVESTMENT RAW DATA ===
{vc_raw}

=== INDIA-SPECIFIC RAW DATA ===
{india_raw}
"""
    api_key = os.environ["AI_API_KEY"]
    model = "gemini-2.5-flash"  # swap for another Gemini model if you prefer
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"content-type": "application/json"},
        params={"key": api_key},
        json={
            "contents": [
                {"parts": [{"text": prompt}]}
            ],
            "generationConfig": {"maxOutputTokens": 8192}
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    candidate = data["candidates"][0]
    finish_reason = candidate.get("finishReason", "")
    if finish_reason == "MAX_TOKENS":
        print("WARNING: Gemini response was truncated (hit MAX_TOKENS). "
              "Consider shortening the requested content or raising maxOutputTokens further.")
    return candidate["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def send_email(html_body):
    cleaned = html_body.strip()

    # Strip ```html fences if present
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()

    # Hard safety net: extract only the outermost <div>...</div>, discarding
    # any stray greeting/preamble text or trailing junk outside it, in case
    # the model doesn't follow the "start/end exactly with div" instruction.
    start = cleaned.find("<div")
    end = cleaned.rfind("</div>")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + len("</div>")]
    else:
        print("WARNING: Could not find <div>...</div> boundaries in AI output — "
              "sending as-is, formatting may be off.")

    msg = MIMEText(cleaned, "html")
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
        summary = summarize_with_ai(startup_raw, vc_raw, india_raw, problem_statements)
    except Exception as e:
        print(f"AI summarization failed: {e}")
        traceback.print_exc()
        # Fall back to raw data if the AI step fails, so the email still sends
        summary = (
            "<div style='font-family:Arial,sans-serif;font-size:14px;'>"
            "<p><strong>AI summarization failed today — sending raw data instead.</strong></p>"
            f"<pre style='white-space:pre-wrap;font-family:monospace;font-size:12px;'>"
            f"=== STARTUP DEMAND ===\n{startup_raw}\n\n=== VC INVESTMENT ===\n{vc_raw}\n\n"
            f"=== INDIA ===\n{india_raw}"
            f"</pre></div>"
        )

    send_email(summary)
    print("Report sent successfully.")


if __name__ == "__main__":
    main()