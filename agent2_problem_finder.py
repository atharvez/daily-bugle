"""
Agent 2: Similar Problem Statement Finder

Runs AFTER main.py in the same workflow (needs: run-report) and consumes
its output — the problem_statements.json artifact containing 2-3 problem
statements extracted from today's raw data.

For each problem statement, this agent searches adjacent-domain sources
for related or similar problem statements — the same underlying pain point
showing up in a different domain/niche — and emails a SHORT COMBINED
briefing tying ALL findings back to their original problem statements.

NEW SOURCES:
  - DuckDuckGo HTML (scoped to startup/forum/Q&A sites)
  - Reddit (r/AskReddit, r/personalfinance, r/freelance, r/digitalnomad,
            r/smallbusiness, r/marketing, r/SaaS)  via RSS
  - Hacker News search (hn.algolia.com — free, no auth)
  - Stack Exchange (api.stackexchange.com — free, no auth)
  - Quora search (DuckDuckGo scoped to quora.com)
  - Dev.to tag feed (free JSON API)
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
# SOURCE 1 — DuckDuckGo HTML (scoped to startup/forum/Q&A sites)
# ---------------------------------------------------------------------------

DDG_SITES = [
    "reddit.com", "indiehackers.com", "news.ycombinator.com",
    "producthunt.com", "quora.com", "stackexchange.com",
    "dev.to", "medium.com",
]


def search_ddg(query_text, max_results=4):
    """Broad DuckDuckGo search — uses key TERMS (not the full quoted statement)
    scoped to startup/forum/Q&A sites to find adjacent-domain pain points."""
    # Extract ≤8 significant words for a broader, less-narrow query
    words = [w for w in query_text.split() if len(w) > 3][:8]
    short_query = " ".join(words)
    site_filter = " OR ".join(f"site:{s}" for s in DDG_SITES)
    query = f"{short_query} problem ({site_filter})"

    resp = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for a in soup.select("a.result__a")[:max_results]:
        title = a.get_text(strip=True)
        href = a.get("href", "")
        # DDG wraps hrefs in a redirect — grab the actual URL from the uddg param
        if "uddg=" in href:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            href = qs.get("uddg", [href])[0]
        if title and href:
            results.append(f"- {title} | {href}")
    return results


# ---------------------------------------------------------------------------
# SOURCE 2 — Hacker News full-text search (Algolia, free, no key)
# ---------------------------------------------------------------------------

def search_hn(query_text, max_results=4):
    """Search HN via Algolia's free API for stories/comments mentioning the problem."""
    words = [w for w in query_text.split() if len(w) > 3][:6]
    short_query = " ".join(words)
    resp = requests.get(
        "https://hn.algolia.com/api/v1/search",
        params={"query": short_query, "tags": "story", "hitsPerPage": max_results},
        timeout=15,
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", [])
    results = []
    for h in hits:
        title = h.get("title", "").strip()
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        if title:
            results.append(f"- [HN] {title} | {url}")
    return results


# ---------------------------------------------------------------------------
# SOURCE 3 — Stack Exchange (free public API, no auth needed)
# ---------------------------------------------------------------------------

STACK_SITES = ["stackoverflow", "startups.stackexchange", "money.stackexchange",
               "workplace.stackexchange", "softwareengineering.stackexchange"]


def search_stackexchange(query_text, max_results=4):
    """Search Stack Exchange questions related to the problem statement."""
    words = [w for w in query_text.split() if len(w) > 3][:6]
    short_query = " ".join(words)
    results = []
    for site in STACK_SITES[:2]:  # limit to 2 sites to stay within free quota
        resp = requests.get(
            "https://api.stackexchange.com/2.3/search/advanced",
            params={
                "q": short_query,
                "site": site.split(".")[0],
                "pagesize": max_results,
                "order": "desc",
                "sort": "votes",
                "filter": "default",
            },
            timeout=15,
        )
        if resp.status_code != 200:
            continue
        items = resp.json().get("items", [])
        for item in items[:max_results]:
            title = item.get("title", "").strip()
            link = item.get("link", "")
            if title and link:
                results.append(f"- [StackExchange/{site}] {title} | {link}")
        if len(results) >= max_results:
            break
    return results


# ---------------------------------------------------------------------------
# SOURCE 4 — Reddit RSS (adjacent subreddits, not the ones main.py covers)
# ---------------------------------------------------------------------------

REDDIT_SUBS = [
    "AskReddit", "personalfinance", "freelance",
    "smallbusiness", "marketing", "SaaS", "digitalnomad",
]


def search_reddit_rss(query_text, max_results=3):
    """Pull top posts from adjacent subreddits and filter for keyword relevance."""
    words = set(w.lower() for w in query_text.split() if len(w) > 3)
    results = []
    for sub in REDDIT_SUBS:
        if len(results) >= max_results:
            break
        try:
            feed = feedparser.parse(
                f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=10"
            )
            for entry in feed.entries:
                title_lower = entry.title.lower()
                if any(w in title_lower for w in words):
                    results.append(f"- [r/{sub}] {entry.title} | {entry.link}")
                    break  # one match per sub is enough
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# SOURCE 5 — Dev.to tag search (free JSON API)
# ---------------------------------------------------------------------------

DEVTO_TAGS = ["startup", "business", "entrepreneur", "productivity", "india"]


def search_devto(query_text, max_results=3):
    """Fetch Dev.to articles by relevant tags, filter by keyword overlap."""
    words = set(w.lower() for w in query_text.split() if len(w) > 3)
    results = []
    for tag in DEVTO_TAGS:
        if len(results) >= max_results:
            break
        try:
            resp = requests.get(
                "https://dev.to/api/articles",
                params={"tag": tag, "per_page": 10, "top": 1},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            for art in resp.json():
                title = art.get("title", "")
                url = art.get("url", "")
                if any(w in title.lower() for w in words) and url:
                    results.append(f"- [Dev.to/{tag}] {title} | {url}")
                    break
        except Exception:
            pass
    return results


# ---------------------------------------------------------------------------
# SOURCE 6 — Quora (DuckDuckGo scoped to quora.com)
# ---------------------------------------------------------------------------

def search_quora(query_text, max_results=3):
    """Search Quora via DuckDuckGo for related questions/pain points."""
    words = [w for w in query_text.split() if len(w) > 3][:6]
    short_query = " ".join(words)
    query = f"site:quora.com {short_query}"
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers=HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        results = []
        for a in soup.select("a.result__a")[:max_results]:
            title = a.get_text(strip=True)
            href = a.get("href", "")
            if "uddg=" in href:
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(href)
                qs = parse_qs(parsed.query)
                href = qs.get("uddg", [href])[0]
            if title and href:
                results.append(f"- [Quora] {title} | {href}")
        return results
    except Exception:
        return []


# ---------------------------------------------------------------------------
# AGGREGATE SEARCH — runs all sources, de-dupes, returns combined list
# ---------------------------------------------------------------------------

def search_similar(problem_statement, max_results_per_source=4):
    """Run all search sources for the given problem statement and return a
    combined de-duplicated list of related findings."""
    all_results = []

    sources = [
        ("DuckDuckGo",    search_ddg,             max_results_per_source),
        ("HackerNews",    search_hn,               max_results_per_source),
        ("StackExchange", search_stackexchange,    max_results_per_source),
        ("Reddit RSS",    search_reddit_rss,        3),
        ("Dev.to",        search_devto,             3),
        ("Quora",         search_quora,             3),
    ]

    for source_name, fn, limit in sources:
        try:
            items = fn(problem_statement, limit)
            if items:
                all_results.extend(items)
                print(f"  [{source_name}] {len(items)} result(s) found.")
            else:
                print(f"  [{source_name}] No results.")
        except Exception as e:
            print(f"  [{source_name}] Error: {type(e).__name__}: {e}")

    # De-dupe by lowercased title (first token before |)
    seen_titles = set()
    deduped = []
    for r in all_results:
        key = r.split("|")[0].lower().strip()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(r)

    if not deduped:
        deduped = ["- No related findings found across sources today."]

    return deduped


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------

def load_problem_statements(path="problem_statements.json"):
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# AI SUMMARIZATION
# ---------------------------------------------------------------------------

def summarize_with_ai(findings_raw):
    prompt = f"""You are a startup analyst. Below is a set of original
problem statements (extracted from today's startup, VC, and tech community
data, already ranked by priority with severity and need scores) paired with
raw search results of similar/related problem statements found in adjacent
domains (HN, Reddit, Quora, StackExchange, Dev.to, DuckDuckGo).

Process the problem statements IN RANK ORDER (highest priority first, as
given). For each, write a short section: restate the original problem in
one line including its rank and scores (e.g. "Rank #1 — Severity 8/10 ·
Need 7/10 · Priority 15/20"), then summarize 2-3 of the most genuinely
similar or related findings — what domain they're in, and why they
represent the same underlying pain point showing up elsewhere. If the raw
search results look irrelevant or noisy, say so honestly rather than
forcing a connection.

Natural, direct analyst tone, no filler. Keep the whole email under 500
words total — hard limit.

STRICT OUTPUT RULES:
- No greeting, no salutation, no placeholder names. Start directly with content.
- No preamble or meta-commentary about what you're about to do.
- Output ONLY valid HTML — no markdown syntax anywhere.
- Never truncate mid-tag or mid-sentence — cut content rather than get cut off.
- The very first characters of your response must be exactly: <div
- The very last characters of your response must be exactly: </div>
- Nothing before the opening <div> or after the closing </div>.

HTML STRUCTURE:
- Single <div> with inline styles, no <html>/<head>/<body>, no classes.
- <h2 style="..."> for a title: "Similar Problem Statements in Adjacent Domains".
- <h3 style="..."> for each original problem statement being expanded on.
- <ul><li style="..."> for related findings under each.
- <a href="URL" style="color:#2563eb;"> for links with real anchor text.
- <strong> for emphasis instead of asterisks.
- font-family: Arial, sans-serif; font-size: 14px; line-height: 1.5;
  color: #1a1a1a.

=== ORIGINAL PROBLEM STATEMENTS + RELATED SEARCH FINDINGS ===
{findings_raw}
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
    data = resp.json()
    candidate = data["candidates"][0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        print("WARNING: Agent 2 Gemini response was truncated (MAX_TOKENS).")
    return candidate["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# EMAIL — sends ONE combined email for ALL problem statements
# ---------------------------------------------------------------------------

def send_email(html_body):
    cleaned = html_body.strip()

    # Strip ```html fences if the model slipped any in
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()

    # Hard safety net: extract only the outermost <div>...</div>
    start = cleaned.find("<div")
    end = cleaned.rfind("</div>")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + len("</div>")]
    else:
        print("WARNING: Could not find <div>...</div> boundaries — sending as-is.")

    msg = MIMEText(cleaned, "html")
    msg["Subject"] = "Similar Problem Statements — Adjacent Domains"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["TO_EMAIL"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        server.send_message(msg)
    print("Agent 2 email sent successfully.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    # ---- 1. Load problem statements from Agent 1 artifact ----
    try:
        problem_statements = load_problem_statements()
    except Exception as e:
        print(f"Could not load problem_statements.json: {e}")
        traceback.print_exc()
        problem_statements = []

    if not problem_statements:
        print("No problem statements available from Agent 1 — nothing to search. Exiting.")
        return

    # ---- 2. Search all sources for each problem statement ----
    findings_blocks = []
    for p in sorted(problem_statements, key=lambda x: x.get("rank", 999)):
        statement = p.get("statement", "")
        print(f"\nSearching sources for: {statement[:80]}...")
        results = search_similar(statement)
        findings_blocks.append(
            f"RANK #{p.get('rank', '?')} — ORIGINAL PROBLEM: {statement}\n"
            f"(Domain: {p.get('domain', 'unknown')}; Evidence: {p.get('evidence', 'n/a')}; "
            f"Severity: {p.get('severity', '?')}/10; Need: {p.get('need', '?')}/10; "
            f"Priority Score: {p.get('priority_score', '?')}/20)\n"
            f"RELATED FINDINGS:\n" + "\n".join(results)
        )

    # ---- 3. Combine ALL findings into ONE block for a single AI call ----
    findings_raw = "\n\n---\n\n".join(findings_blocks)

    # ---- 4. Summarize with AI (one combined call → one combined email) ----
    try:
        summary = summarize_with_ai(findings_raw)
    except Exception as e:
        print(f"AI summarization failed: {e}")
        traceback.print_exc()
        # Fallback: send the raw findings as plain pre-formatted HTML
        summary = (
            "<div style='font-family:Arial,sans-serif;font-size:14px;'>"
            "<p><strong>AI summarization failed today — sending raw findings instead.</strong></p>"
            f"<pre style='white-space:pre-wrap;font-family:monospace;font-size:12px;'>{findings_raw}</pre>"
            "</div>"
        )

    # ---- 5. Send ONE combined email ----
    send_email(summary)


if __name__ == "__main__":
    main()