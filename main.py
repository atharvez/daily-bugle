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
    edges = resp["data"]["posts"]["edges"]
    return [f"- {e['node']['name']} — {e['node']['tagline']} "
            f"({e['node']['votesCount']} votes) {e['node']['url']}" for e in edges]


def fetch_reddit(subreddits=("startups", "Entrepreneur"), limit=5):
    """Top daily posts from given subreddits via Reddit's public JSON endpoint.
    No auth/app registration required — works for read-only public data.
    More rate-limit sensitive than the OAuth API, but fine for one daily pull.
    If you later get API credentials, swap this for the OAuth version for
    higher reliability."""
    results = []
    for sub in subreddits:
        resp = requests.get(
            f"https://www.reddit.com/r/{sub}/top.json?t=day&limit={limit}",
            headers=HEADERS, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        for post in data["data"]["children"]:
            d = post["data"]
            results.append(f"- [r/{sub}] {d['title']} ({d['ups']} upvotes) "
                            f"https://reddit.com{d['permalink']}")
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
# VC INVESTMENT SOURCES
# ---------------------------------------------------------------------------

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
# AI SUMMARIZATION
# ---------------------------------------------------------------------------

def summarize_with_ai(startup_raw, vc_raw):
    prompt = f"""You are producing a concise daily briefing for a startup founder.

Below is raw scraped data from two categories. Write a clean, skimmable
summary with two sections: "Startup Demand Signals" and "VC Investment
Activity". Under each, pull out the 5-8 most notable/high-signal items,
group related items together, drop noise/duplicates, and keep it under
400 words total. Use short bullet points. Include source links where present.

=== STARTUP DEMAND RAW DATA ===
{startup_raw}

=== VC INVESTMENT RAW DATA ===
{vc_raw}
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
            ]
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ---------------------------------------------------------------------------
# EMAIL
# ---------------------------------------------------------------------------

def send_email(body):
    msg = MIMEText(body)
    msg["Subject"] = "Daily Startup & VC Report"
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

    startup_raw = "\n\n".join(startup_sections)
    vc_raw = "\n\n".join(vc_sections)

    try:
        summary = summarize_with_ai(startup_raw, vc_raw)
    except Exception as e:
        print(f"AI summarization failed: {e}")
        traceback.print_exc()
        # Fall back to raw data if the AI step fails, so the email still sends
        summary = (
            "AI summarization failed today — sending raw data instead.\n\n"
            f"=== STARTUP DEMAND ===\n{startup_raw}\n\n"
            f"=== VC INVESTMENT ===\n{vc_raw}"
        )

    send_email(summary)
    print("Report sent successfully.")


if __name__ == "__main__":
    main()