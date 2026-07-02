# SuprAgent — Daily Startup & VC Report

A lightweight Python script that aggregates startup demand signals and VC investment activity from multiple sources, summarizes them with an AI model (Gemini), and emails the result daily.

## Sources

**Startup Demand**
- Hacker News / YC (official API)
- Product Hunt (GraphQL API)
- Reddit r/startups & r/Entrepreneur
- Indie Hackers (scrape)
- G2 Trending (scrape)

**VC Investment Activity**
- a16z blog (RSS)
- YC Blog (RSS)
- Sequoia articles (scrape)
- Peak XV insights (scrape)

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set environment variables
```bash
AI_API_KEY=your_gemini_api_key
PRODUCTHUNT_TOKEN=your_producthunt_oauth_token
SMTP_USER=your_gmail_address@gmail.com
SMTP_PASS=your_gmail_app_password
TO_EMAIL=recipient@example.com
```

> **Note:** For Gmail, use an [App Password](https://support.google.com/accounts/answer/185833), not your main account password.

### 3. Run
```bash
python main.py
```

### 4. Automate (daily schedule)
See `daily-report.yml` for a GitHub Actions workflow that runs the script on a schedule.

## Configuration

The `daily-report.yml` GitHub Actions workflow schedules the job. Adjust the `cron` expression to your preferred time.

All credentials are read from environment variables — never hard-coded. Store them as [GitHub Actions Secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets) when running via CI.
