# Daily Bugle

A Python-powered automated news aggregation and daily digest generator.

## Overview

Daily Bugle fetches, processes, and summarizes news from multiple sources, delivering a clean daily briefing. Named after the iconic newspaper from Spider-Man -- it cuts through the noise.

## Tech Stack

- Language: Python 3.10+
- HTTP: requests / httpx
- Parsing: BeautifulSoup4 / feedparser
- Scheduling: schedule or cron

## Features

- Multi-source aggregation -- pull from RSS feeds, APIs, and web pages
- AI summaries -- condense long articles into key points
- Digest output -- markdown, HTML, or email format
- Topic filtering -- customizable keyword and category filters
- Scheduled runs -- set it and forget it

## Getting Started

```bash
git clone https://github.com/atharvez/daily-bugle.git
cd daily-bugle
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## License

MIT (c) Atharva Desai