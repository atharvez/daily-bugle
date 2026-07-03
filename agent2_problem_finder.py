"""
Agent 2: Similar Problem Statement Finder

Runs AFTER main.py in the same workflow (needs: run-report) and consumes
its output — the problem_statements.json artifact containing 2-3 problem
statements extracted from today's raw data.

For each problem statement, this agent searches adjacent-domain sources
(Reddit communities outside the ones main.py already covers, plus a
DuckDuckGo HTML search scoped to startup/forum sites) for related or
similar problem statements — the same underlying pain point showing up
in a different domain/niche — and emails a short briefing tying each
finding back to the original problem statement.
"""

import os
import json
import smtplib
import traceback
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DailyReportBot/1.0; +https://github.com/)"
}

# Sites to scope the similarity search to — general startup/founder/discussion
# communities, kept distinct from what main.py already scrapes directly.
SEARCH_SITES = ["reddit.com", "indiehackers.com", "news.ycombinator.com", "producthunt.com"]


def load_problem_statements(path="problem_statements.json"):
    with open(path) as f:
        return json.load(f)


def search_similar(problem_statement, max_results=4):
    """Search DuckDuckGo's HTML endpoint (no API key required) for the
    problem statement text, scoped to startup/discussion sites, to find
    similar problem statements surfacing in adjacent domains."""
    site_filter = " OR ".join(f"site:{s}" for s in SEARCH_SITES)
    query = f'"{problem_statement}" ({site_filter})'
    resp = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=HEADERS,
        timeout=15,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    results = []
    for a in soup.select("a.result__a")[:max_results]:
        title = a.get_text(strip=True)
        href = a.get("href")
        if title and href:
            results.append(f"- {title} {href}")
    if not results:
        raise RuntimeError("No search results returned (query may be too narrow or blocked)")
    return results


def safe_search(problem_statement):
    try:
        return search_similar(problem_statement)
    except Exception as e:
        print(f"WARNING: search failed for '{problem_statement[:60]}...': {e}")
        traceback.print_exc()
        return [f"- No related findings today (search error: {type(e).__name__})."]


def summarize_with_ai(findings_raw):
    prompt = f"""You are a startup analyst. Below is a set of original
problem statements (found in today's main startup/VC data pull, already
ranked by priority with severity and need scores) paired with raw search
results of similar/related problem statements found in adjacent domains.

Process the problem statements IN RANK ORDER (highest priority first, as
given). For each, write a short section: restate the original problem in
one line including its rank and scores (e.g. "Rank #1 — Severity 8/10 ·
Need 7/10 · Priority 15/20"), then summarize 2-3 of the most genuinely
similar or related findings — what domain they're in, and why they
represent the same underlying pain point showing up elsewhere. If the raw
search results look irrelevant or noisy, say so honestly rather than
forcing a connection.

Natural, direct analyst tone, no filler. Keep the whole email under 400
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


def send_email(html_body):
    cleaned = html_body.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    cleaned = cleaned.strip()

    start = cleaned.find("<div")
    end = cleaned.rfind("</div>")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + len("</div>")]
    else:
        print("WARNING: Could not find <div>...</div> boundaries — sending as-is.")

    msg = MIMEText(cleaned, "html")
    msg["Subject"] = "Similar Problem Statements — India-Centric, Adjacent Domains"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["TO_EMAIL"]

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        server.send_message(msg)


def main():
    try:
        problem_statements = load_problem_statements()
    except Exception as e:
        print(f"Could not load problem_statements.json: {e}")
        traceback.print_exc()
        problem_statements = []

    if not problem_statements:
        print("No problem statements available from Agent 1 — nothing to search. Exiting.")
        return

    findings_blocks = []
    # Process in rank order (already sorted by priority_score in main.py)
    for p in sorted(problem_statements, key=lambda x: x.get("rank", 999)):
        statement = p.get("statement", "")
        results = safe_search(statement)
        findings_blocks.append(
            f"RANK #{p.get('rank', '?')} — ORIGINAL PROBLEM: {statement}\n"
            f"(Domain: {p.get('domain', 'unknown')}; Evidence: {p.get('evidence', 'n/a')}; "
            f"Severity: {p.get('severity', '?')}/10; Need: {p.get('need', '?')}/10; "
            f"Priority Score: {p.get('priority_score', '?')}/20)\n"
            f"RELATED FINDINGS:\n" + "\n".join(results)
        )

    findings_raw = "\n\n---\n\n".join(findings_blocks)

    try:
        summary = summarize_with_ai(findings_raw)
    except Exception as e:
        print(f"AI summarization failed: {e}")
        traceback.print_exc()
        summary = (
            "<div style='font-family:Arial,sans-serif;font-size:14px;'>"
            "<p><strong>AI summarization failed today — sending raw data instead.</strong></p>"
            f"<pre style='white-space:pre-wrap;font-family:monospace;font-size:12px;'>{findings_raw}</pre>"
            "</div>"
        )

    send_email(summary)
    print("Agent 2 report sent successfully.")


if __name__ == "__main__":
    main()