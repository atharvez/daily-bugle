# Daily Bugle ðŸ“°

A Python-powered automated news aggregation and daily digest generator.

## Overview

Daily Bugle fetches, processes, and summarizes news from multiple sources, delivering a clean daily briefing. Named after the iconic newspaper from Spider-Man, it cuts through the noise to deliver what matters.

## Tech Stack

- **Language:** Python 3.10+
- **HTTP:** requests / httpx
- **Parsing:** BeautifulSoup4 / feedparser
- **Scheduling:** schedule or cron

## Features

- ðŸ“¡ **Multi-Source Aggregation** â€” Pull from RSS feeds, APIs, and web pages
- ðŸ¤– **AI Summaries** â€” Condense long articles into key points
- ðŸ“§ **Digest Output** â€” Generate daily newsletters (email, markdown, HTML)
- ðŸ”– **Topic Filtering** â€” Customizable keyword and category filters
- â° **Scheduled Runs** â€” Set it and forget it

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

MIT Â© [Atharva Desai](https://github.com/atharvez)