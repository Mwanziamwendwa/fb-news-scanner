"""
Kefa's Financial & Employment News Scanner
Scans free RSS/Google News feeds for content relevant to salaried workers,
SACCOs, microfinance, and general Kenyan financial news, then posts to
a Facebook Page via the Graph API.

Environment variables required (set as GitHub Secrets):
  PAGE_ACCESS_TOKEN
  PAGE_ID
"""

import os
import time
import requests
import feedparser
from datetime import datetime, timedelta
import pytz

# ---------- CONFIG ----------

PAGE_ID = os.environ["PAGE_ID"]
PAGE_ACCESS_TOKEN = os.environ["PAGE_ACCESS_TOKEN"]
GRAPH_API_VERSION = "v20.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PAGE_ID}/feed"

NAIROBI_TZ = pytz.timezone("Africa/Nairobi")

# RSS / Google News feeds covering the broadened target audience
FEEDS = [
    "https://news.google.com/rss/search?q=TSC+Kenya+teachers&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=civil+servants+Kenya+salary&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=SACCO+Kenya&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=microfinance+Kenya&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=CBK+Central+Bank+Kenya+interest+rate&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=bank+loan+Kenya&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=World+Bank+Kenya+economy&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=KRA+Kenya+tax&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=county+government+Kenya+salary&hl=en-KE&gl=KE&ceid=KE:en",
    "https://news.google.com/rss/search?q=SME+business+loan+Kenya&hl=en-KE&gl=KE&ceid=KE:en",
]

KEYWORDS = [
    "tsc", "teacher", "civil servant", "county government", "salary",
    "payslip", "cba", "sacco", "chama", "microfinance", "kra", "tax",
    "sme", "business loan", "bank loan", "interest rate", "cbk",
    "central bank", "world bank", "inflation", "cost of living",
    "deposit", "savings", "credit", "mortgage", "shares", "stock",
]

MAX_POSTS_PER_RUN = 4
LOOKBACK_HOURS = 10  # only consider articles published within this window

# ---------- HELPERS ----------

def is_relevant(title, summary):
    text = f"{title} {summary}".lower()
    return any(kw in text for kw in KEYWORDS)

def is_recent(published_parsed):
    if not published_parsed:
        return True  # if no date info, don't discard it
    published_dt = datetime(*published_parsed[:6], tzinfo=pytz.utc)
    cutoff = datetime.now(pytz.utc) - timedelta(hours=LOOKBACK_HOURS)
    return published_dt >= cutoff

def nairobi_time_label():
    now = datetime.now(NAIROBI_TZ)
    return now.strftime("%A, %d %B %Y — %I:%M %p (Nairobi time)")

def build_post_text(title, link):
    time_label = nairobi_time_label()
    return (
        f"📢 {title}\n\n"
        f"🕒 {time_label}\n\n"
        f"Here's what you need to know today. Stay informed and plan your finances wisely.\n\n"
        f"🔗 Read more: {link}\n\n"
        f"#KenyaNews #TSC #SACCO #Teachers #CivilServants #Finance"
    )

def post_to_facebook(message):
    payload = {
        "message": message,
        "access_token": PAGE_ACCESS_TOKEN,
    }
    response = requests.post(GRAPH_URL, data=payload, timeout=30)
    if response.status_code == 200:
        print(f"[OK] Posted: {response.json()}")
        return True
    else:
        print(f"[ERROR] Failed to post: {response.status_code} {response.text}")
        return False

# ---------- MAIN ----------

def main():
    seen_titles = set()
    posts_made = 0

    for feed_url in FEEDS:
        if posts_made >= MAX_POSTS_PER_RUN:
            break

        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[WARN] Could not parse feed {feed_url}: {e}")
            continue

        for entry in feed.entries:
            if posts_made >= MAX_POSTS_PER_RUN:
                break

            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "")
            published_parsed = entry.get("published_parsed")

            if not title or not link:
                continue
            if title in seen_titles:
                continue
            if not is_relevant(title, summary):
                continue
            if not is_recent(published_parsed):
                continue

            message = build_post_text(title, link)
            success = post_to_facebook(message)

            seen_titles.add(title)
            if success:
                posts_made += 1
                time.sleep(5)  # small delay between posts

    print(f"Done. Posts made this run: {posts_made}")

if __name__ == "__main__":
    main()
