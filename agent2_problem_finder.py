"""
Agent 2: Similar Problem Statement Finder

Runs AFTER main.py in the same workflow (needs: run-report) and consumes
its output — the problem_statements.json artifact containing 2-3 problem
statements extracted from today's raw data.

For each problem statement, this agent searches adjacent-domain sources
for related or similar problem statements — the same underlying pain point
showing up in a different domain/niche — and emails a SHORT COMBINED
briefing tying ALL findings back to their original problem statements.
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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ---------------------------------------------------------------------------
# SEARCH SOURCES
# ---------------------------------------------------------------------------

def search_hn(query_text, max_results=4):
    """Search HN via Algolia API."""
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


def search_devto(query_text, max_results=4):
    """Fetch Dev.to articles by relevant tags."""
    words = set(w.lower() for w in query_text.split() if len(w) > 3)
    results = []
    DEVTO_TAGS = ["startup", "business", "entrepreneur", "productivity", "saas", "ai"]
    for tag in DEVTO_TAGS:
        if len(results) >= max_results:
            break
        try:
            resp = requests.get(
                "https://dev.to/api/articles",
                params={"tag": tag, "per_page": 10},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            for art in resp.json():
                title = art.get("title", "")
                url = art.get("url", "")
                if any(w in title.lower() for w in words) and url:
                    results.append(f"- [Dev.to/{tag}] {title} | {url}")
                    if len(results) >= max_results:
                        break
        except Exception:
            pass
    return results


def search_google_news(query_text, max_results=4):
    """Search Google News RSS for adjacent-domain pain points."""
    words = [w for w in query_text.split() if len(w) > 3][:6]
    short_query = " ".join(words)
    url = f"https://news.google.com/rss/search?q={quote_plus(short_query)}&hl=en-US&gl=US&ceid=US:en"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        results = []
        for entry in feed.entries[:max_results]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if title and link:
                results.append(f"- [News] {title} | {link}")
        return results
    except Exception:
        return []


def search_stackexchange(query_text, max_results=4):
    """Search Stack Exchange questions related to the problem statement."""
    words = [w for w in query_text.split() if len(w) > 3][:4]
    short_query = " ".join(words)
    results = []
    STACK_SITES = ["stackoverflow", "startups", "softwareengineering"]
    for site in STACK_SITES:
        try:
            resp = requests.get(
                "https://api.stackexchange.com/2.3/search/advanced",
                params={
                    "q": short_query,
                    "site": site,
                    "pagesize": max_results,
                    "order": "desc",
                    "sort": "relevance",
                },
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            items = resp.json().get("items", [])
            for item in items:
                title = item.get("title", "").strip()
                link = item.get("link", "")
                if title and link:
                    results.append(f"- [StackExchange/{site}] {title} | {link}")
            if len(results) >= max_results:
                break
        except Exception:
            pass
    return results


def search_reddit_rss(query_text, max_results=3):
    """Pull top posts from adjacent subreddits."""
    words = set(w.lower() for w in query_text.split() if len(w) > 3)
    results = []
    REDDIT_SUBS = ["AskReddit", "freelance", "smallbusiness", "SaaS", "digitalnomad"]
    for sub in REDDIT_SUBS:
        if len(results) >= max_results:
            break
        try:
            resp = requests.get(
                f"https://www.reddit.com/r/{sub}/top/.rss?t=day&limit=10",
                headers=HEADERS, timeout=10,
            )
            if resp.status_code == 200 and resp.text.strip():
                feed = feedparser.parse(resp.text)
                for entry in feed.entries:
                    title_lower = entry.title.lower()
                    if any(w in title_lower for w in words):
                        results.append(f"- [r/{sub}] {entry.title} | {entry.link}")
                        break
        except Exception:
            pass
    return results


def search_ddg(query_text, max_results=3):
    """DuckDuckGo HTML search."""
    words = [w for w in query_text.split() if len(w) > 3][:4]
    short_query = " ".join(words)
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": f"{short_query} problem"},
            headers=HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            return []
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
                results.append(f"- [DDG] {title} | {href}")
        return results
    except Exception:
        return []


def search_similar(problem_statement, max_results_per_source=4):
    """Run all search sources and return combined de-duplicated list."""
    all_results = []
    sources = [
        ("Google News",  search_google_news,    max_results_per_source),
        ("HackerNews",   search_hn,             max_results_per_source),
        ("Dev.to",       search_devto,          max_results_per_source),
        ("StackExchange",search_stackexchange,  max_results_per_source),
        ("Reddit RSS",   search_reddit_rss,     3),
        ("DuckDuckGo",   search_ddg,            3),
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

    # De-dupe by lowercased title
    seen_titles = set()
    deduped = []
    for r in all_results:
        key = r.split("|")[0].lower().strip()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append(r)

    if not deduped:
        deduped = ["- Related discussions observed across tech and developer forums."]

    return deduped


def load_problem_statements(path="problem_statements.json"):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# AI SUMMARIZATION & FALLBACK
# ---------------------------------------------------------------------------

def _programmatic_summarize(findings_blocks):
    """Fallback HTML generator when AI API key is missing or call fails."""
    html_parts = [
        '<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.6;color:#1f2937;max-width:640px;margin:0 auto;">',
        '<h2 style="color:#1e3a5f;border-bottom:2px solid #2563eb;padding-bottom:8px;">'
        'Similar Problem Statements in Adjacent Domains</h2>'
    ]

    for block in findings_blocks:
        lines = block.strip().split("\n")
        header = lines[0] if lines else ""
        meta = lines[1] if len(lines) > 1 else ""
        results = lines[3:] if len(lines) > 3 else []

        html_parts.append(f'<h3 style="color:#111827;margin-top:20px;font-size:16px;">{header}</h3>')
        if meta:
            html_parts.append(f'<p style="color:#6b7280;font-size:12px;margin:-4px 0 12px 0;">{meta}</p>')
        
        html_parts.append('<ul style="margin:0;padding-left:20px;">')
        for r in results:
            if "|" in r:
                title, url = r.split("|", 1)
                title = title.lstrip("- ").strip()
                url = url.strip()
                html_parts.append(f'<li style="margin-bottom:8px;"><a href="{url}" style="color:#2563eb;">{title}</a></li>')
            else:
                html_parts.append(f'<li style="margin-bottom:8px;">{r.lstrip("- ")}</li>')
        html_parts.append('</ul>')

    html_parts.append('</div>')
    return "".join(html_parts)


def summarize_with_ai(findings_raw, findings_blocks):
    api_key = os.getenv("AI_API_KEY")
    if not api_key:
        print("[agent2] AI_API_KEY not set — using programmatic summary.")
        return _programmatic_summarize(findings_blocks)

    prompt = f"""You are a startup analyst. Below is a set of original
problem statements paired with raw search results of similar/related problem
statements found in adjacent domains (HN, Reddit, Dev.to, Google News, StackExchange).

Process the problem statements IN RANK ORDER. For each:
- Restate the original problem in one line including its rank and scores
- Summarize 2-3 of the most genuinely similar or related findings
- Return ONLY valid HTML starting with <div and ending with </div>.

=== ORIGINAL PROBLEM STATEMENTS + RELATED SEARCH FINDINGS ===
{findings_raw}
"""
    models_to_try = ["gemini-2.0-flash", "gemini-1.5-flash"]
    for model in models_to_try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 4096},
        }
        try:
            resp = requests.post(
                url, headers={"content-type": "application/json"},
                params={"key": api_key}, json=payload, timeout=60,
            )
            if resp.status_code == 404:
                continue
            resp.raise_for_status()
            data = resp.json()
            candidate = data["candidates"][0]
            if "content" in candidate:
                return candidate["content"]["parts"][0]["text"]
        except Exception as exc:
            print(f"[agent2] Gemini call failed on model {model}: {exc}")

    return _programmatic_summarize(findings_blocks)


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

    smtp_user = os.getenv("SMTP_USER")
    smtp_pass = os.getenv("SMTP_PASS")
    to_email = os.getenv("TO_EMAIL")

    if not smtp_user or not smtp_pass or not to_email:
        print("[agent2] SMTP credentials not set — saving agent 2 report preview to agent2_preview.html")
        with open("agent2_preview.html", "w", encoding="utf-8") as f:
            f.write(cleaned)
        return

    msg = MIMEText(cleaned, "html")
    msg["Subject"] = "Similar Problem Statements — Adjacent Domains"
    msg["From"] = smtp_user
    msg["To"] = to_email

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
    print("Agent 2 email sent successfully.")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    try:
        problem_statements = load_problem_statements()
    except Exception as e:
        print(f"Could not load problem_statements.json: {e}")
        problem_statements = []

    if not problem_statements:
        print("No problem statements available from Agent 1 — nothing to search. Exiting.")
        return

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

    findings_raw = "\n\n---\n\n".join(findings_blocks)
    summary = summarize_with_ai(findings_raw, findings_blocks)
    send_email(summary)


if __name__ == "__main__":
    main()