"""
Kenya News Scanner + Facebook Page Publisher

Reads RSS feeds, finds recent stories, deduplicates them, flags sensitive
content, fetches up to ~1000 words of the full article and paraphrases a
short topic + under-200-word summary (via Groq, with a safe fallback if
any of that is unavailable), and can publish it to a Facebook Page.

IMPORTANT:
- Never put your Facebook token or Groq key in this file.
- Use GitHub Secrets:
    FACEBOOK_PAGE_ID
    FACEBOOK_PAGE_ACCESS_TOKEN
    GROQ_API_KEY   (optional — enables paraphrased topic + summary)
- The token previously pasted into chat should be revoked/rotated.
- This script does not copy article bodies verbatim. If Groq is
  configured, it posts an original topic line + paraphrased summary
  built from the fetched article (or the RSS snippet if the fetch
  fails). Otherwise it posts a cleaned-up version of the title/snippet.
  Either way, there's no outlet attribution line and no link text in
  the post body — the original link is still passed to Facebook
  separately as the post's link-preview target.
"""

import os
import json
import socket
import html
import re
import time
from datetime import datetime, timedelta

import feedparser
import pytz
import requests
import trafilatura


NAIROBI_TZ = pytz.timezone("Africa/Nairobi")

SUGGESTED_POSTS_FILE = "suggested_posts.md"
POSTED_LOG_FILE = "posted_log.json"
FACEBOOK_POST_LOG_FILE = "facebook_post_log.json"

LOOKBACK_HOURS = 48
MAX_SUGGESTIONS_PER_RUN = 25
MAX_FACEBOOK_POSTS_PER_RUN = 5
FEED_TIMEOUT_SECONDS = 15
FACEBOOK_TIMEOUT_SECONDS = 30
GROQ_TIMEOUT_SECONDS = 20
ARTICLE_FETCH_TIMEOUT_SECONDS = 15

# Full article text is trimmed to this many words before being sent to
# Groq, to keep prompts small and predictable.
MAX_ARTICLE_WORDS = 1000

# Target length for the Groq-generated summary.
MAX_SUMMARY_WORDS = 200

# Set to false if you want suggestions only.
AUTO_POST_TO_FACEBOOK = True

FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "").strip()
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()

# Optional. If omitted, the script uses the unversioned Graph endpoint.
FACEBOOK_GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "").strip()

# Optional. If empty, the script skips paraphrasing and uses the
# cleaned-text fallback instead — it never fails the run over this.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-20b").strip()

FEED_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; KenyaNewsScanner/2.0; "
        "+https://github.com/)"
    )
}

SENSITIVE_TERMS = [
    "autopsy", "mutilated", "beheaded", "dismembered", "gore",
    "graphic images", "explicit video", "child abuse", "defiled",
    "gang rape", "mob justice", "lynched",
]

# Entries in posted_log.json that look like this (underscore-separated,
# no spaces, no punctuation) are almost certainly category/taxonomy keys
# from some other process, not real RSS titles. normalize_title() never
# produces this shape from a real headline. We quarantine them on load so
# they can't silently block or corrupt future dedup, and warn loudly so
# the source of the pollution can be tracked down.
_SUSPICIOUS_SLUG_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")

FEEDS = [
    # Google News RSS
    {"category": "Kenya Latest", "url": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSkwyMHZNREU1Y21jMUVnVmxiaTFIUWlnQVAB?hl=en-KE&gl=KE&ceid=KE%3Aen"},
    {"category": "Politics", "url": "https://news.google.com/rss/search?q=Kenya+politics&hl=en-KE&gl=KE&ceid=KE:en"},
    {"category": "Business", "url": "https://news.google.com/rss/search?q=Kenya+business+economy&hl=en-KE&gl=KE&ceid=KE:en"},
    {"category": "Business Topic", "url": "https://news.google.com/rss/topics/CAAqKggKIiRDQkFTRlFvSUwyMHZNRGx6TVdZU0JXVnVMVWRDR2dKTFJTZ0FQAQ?hl=en-KE&gl=KE&ceid=KE:en"},
    {"category": "Entertainment", "url": "https://news.google.com/rss/search?q=Kenya+entertainment+celebrity&hl=en-KE&gl=KE&ceid=KE:en"},
    {"category": "Sports", "url": "https://news.google.com/rss/search?q=Kenya+sports+football&hl=en-KE&gl=KE&ceid=KE:en"},
    {"category": "Trending", "url": "https://news.google.com/rss/search?q=Kenya+viral+trending&hl=en-KE&gl=KE&ceid=KE:en"},

    # Kenyan news sites
    {"category": "Nairobi Leo", "url": "https://nairobileo.co.ke/feed"},
    {"category": "SPM Buzz", "url": "https://spmbuzz.com/feed/"},
    {"category": "Ghafla", "url": "https://www.ghafla.co.ke/feed/"},
    {"category": "Ghafla KE", "url": "https://ghafla.co.ke/ke/feed"},
    {"category": "K24 Digital", "url": "https://k24.digital/feed"},
    {"category": "KBC Digital", "url": "https://kbc.co.ke/feed"},
    {"category": "NTV Kenya", "url": "https://ntvkenya.co.ke/feed/"},
    {"category": "Kenyans.co.ke", "url": "https://www.kenyans.co.ke/feeds/news"},
    {"category": "Nation Africa", "url": "https://nation.africa/kenya/rss.xml"},
    {"category": "Business Daily Africa", "url": "https://www.businessdailyafrica.com/service/rss/bd/1939132/feed.rss"},
    {"category": "Kenyan Wall Street", "url": "https://kenyanwallstreet.com/feed/"},
    {"category": "Kenya News Agency", "url": "https://www.kenyanews.go.ke/feed/"},
    {"category": "Taifa Leo", "url": "https://taifaleo.nation.co.ke/feed"},
    {"category": "Nairobi Wire", "url": "https://nairobiwire.com/feed"},
    {"category": "Diaspora Messenger", "url": "https://diasporamessenger.com/feed/"},
    {"category": "Mwakilishi", "url": "https://mwakilishi.com/feed"},
    {"category": "Sharp Daily", "url": "https://sharpdaily.co.ke/feed/"},
    {"category": "News Trends KE", "url": "https://newstrends.co.ke/feed/"},
    {"category": "Sauce Kenya", "url": "https://sauce.co.ke/feed/"},
    {"category": "The Kenya Times", "url": "https://thekenyatimes.com/feed/"},
    {"category": "Aipate News", "url": "https://aipate.com/category/news/feed"},
    {"category": "KenyaMOJA", "url": "https://www.kenyamoja.com/news/nairobi-leo/feed"},
    {"category": "Nairobi Gossip Club", "url": "https://nairobigossipclub.co.ke/feeds"},
    {"category": "Education News", "url": "https://educationnews.co.ke/feed/"},

    # Standard Media
    {"category": "Standard Headlines", "url": "https://www.standardmedia.co.ke/rss/headlines.php"},
    {"category": "Standard Kenya", "url": "https://www.standardmedia.co.ke/rss/kenya.php"},
    {"category": "Standard Politics", "url": "https://www.standardmedia.co.ke/rss/politics.php"},
    {"category": "Standard Sports", "url": "https://www.standardmedia.co.ke/rss/sports.php"},
    {"category": "Standard World", "url": "https://www.standardmedia.co.ke/rss/world.php"},

    # Capital FM
    {"category": "Capital FM News", "url": "https://capitalfm.africa/news/feed/"},
    {"category": "Capital FM Sports", "url": "https://capitalfm.africa/sports/feed/"},
    {"category": "Capital FM Lifestyle", "url": "https://capitalfm.africa/lifestyle/feed/"},
    {"category": "Capital FM Business", "url": "https://capitalfm.africa/business/feed/"},
    {"category": "Capital FM Kenya", "url": "https://www.capitalfm.co.ke/news/feed/"},

    # Getembe TV
    {"category": "Getembe Latest", "url": "https://getembetv.co.ke/rss/latest-posts"},
    {"category": "Getembe News", "url": "https://getembetv.co.ke/rss/category/news"},
    {"category": "Getembe Business", "url": "https://getembetv.co.ke/rss/category/business"},
    {"category": "Getembe Education", "url": "https://getembetv.co.ke/rss/category/education"},
    {"category": "Getembe Politics", "url": "https://getembetv.co.ke/rss/category/politics"},
    {"category": "Getembe Health", "url": "https://getembetv.co.ke/rss/category/health"},

    # Viral Tea
    {"category": "Viral Tea Latest", "url": "https://viraltea.co.ke/rss/latest-posts"},
    {"category": "Viral Tea News", "url": "https://viraltea.co.ke/rss/category/news"},
    {"category": "Viral Tea Breaking", "url": "https://viraltea.co.ke/rss/category/breaking"},
    {"category": "Viral Tea National", "url": "https://viraltea.co.ke/rss/category/national"},
    {"category": "Viral Tea Local", "url": "https://viraltea.co.ke/rss/category/local"},

    # Kenyapedia
    {"category": "Kenyapedia Latest", "url": "https://www.kenyapedia.co.ke/rss/latest-posts"},
    {"category": "Kenyapedia Jobs", "url": "https://www.kenyapedia.co.ke/rss/category/jobs"},
    {"category": "Kenyapedia Money", "url": "https://www.kenyapedia.co.ke/rss/category/money-and-finances"},
    {"category": "Kenyapedia Grants", "url": "https://www.kenyapedia.co.ke/rss/category/business-grants-and-financing"},
    {"category": "Kenyapedia Debt", "url": "https://www.kenyapedia.co.ke/rss/category/debt-and-borrowing"},
    {"category": "Kenyapedia Economy", "url": "https://www.kenyapedia.co.ke/rss/category/kenya-economy"},
    {"category": "Kenyapedia Education", "url": "https://www.kenyapedia.co.ke/rss/category/education-funding"},
    {"category": "Kenyapedia Taxes", "url": "https://www.kenyapedia.co.ke/rss/category/taxes-50"},
    {"category": "Kenyapedia Investments", "url": "https://www.kenyapedia.co.ke/rss/category/savings-and-investments"},
    {"category": "Kenyapedia Recent News", "url": "https://www.kenyapedia.co.ke/rss/category/recent-news"},
]


def normalize_title(title):
    return " ".join(title.lower().split())


def load_json_set(filename, quarantine_suspicious=False):
    """
    Load a JSON list/dict of strings into a set.

    If quarantine_suspicious is True, entries that look like taxonomy/
    category slugs (e.g. "sacco_chama", "banking_microfinance") rather
    than normalized article titles are dropped and reported, since
    normalize_title() never produces that shape from a real RSS title.
    This guards the dedup log against pollution from another process
    writing to the same file.
    """
    if not os.path.exists(filename):
        return set()
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            values = set(data)
        elif isinstance(data, dict):
            values = set(data.keys())
        else:
            print(f"[WARN] Unexpected structure in {filename}; starting fresh.")
            return set()
    except (OSError, json.JSONDecodeError, ValueError):
        print(f"[WARN] Could not read {filename}; starting fresh.")
        return set()

    if quarantine_suspicious:
        suspicious = {v for v in values if _SUSPICIOUS_SLUG_RE.match(v)}
        if suspicious:
            print(
                f"[WARN] {filename}: ignoring {len(suspicious)} entries that "
                f"look like category slugs, not article titles (these did "
                f"not come from this script's normalize_title()): "
                f"{sorted(suspicious)}"
            )
            values -= suspicious

    return values


def save_json_set(filename, values):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(sorted(values), f, indent=2, ensure_ascii=False)


def is_recent(published_parsed):
    if not published_parsed:
        return True
    try:
        published_dt = datetime(*published_parsed[:6], tzinfo=pytz.utc)
    except (TypeError, ValueError, OverflowError):
        return True
    cutoff = datetime.now(pytz.utc) - timedelta(hours=LOOKBACK_HOURS)
    return published_dt >= cutoff


def clean_text(value):
    return " ".join(str(value or "").split())


def strip_html(value):
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return clean_text(value)


def is_sensitive(title, summary):
    text = f"{title} {summary}".lower()
    return any(term in text for term in SENSITIVE_TERMS)


def nairobi_timestamp():
    return datetime.now(NAIROBI_TZ).strftime("%Y-%m-%d %H:%M")


def parse_feed_safely(feed_url):
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(FEED_TIMEOUT_SECONDS)
    try:
        return feedparser.parse(
            feed_url,
            request_headers=FEED_REQUEST_HEADERS
        )
    finally:
        socket.setdefaulttimeout(old_timeout)


def source_name_from_title(title, category):
    if " - " in title:
        possible = title.rsplit(" - ", 1)[-1].strip()
        if possible:
            return possible
    return category


def fetch_article_text(link):
    """
    Fetches the publisher's article page and extracts up to
    MAX_ARTICLE_WORDS words of the main body text via trafilatura.
    Returns None (never raises) if the fetch or extraction fails for
    any reason — sites that block scraping, paywall, time out, or have
    layouts trafilatura can't parse. Callers fall back to the RSS
    snippet in that case.
    """
    try:
        response = requests.get(
            link,
            headers=FEED_REQUEST_HEADERS,
            timeout=ARTICLE_FETCH_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[WARN] Could not fetch article page: {exc}")
        return None

    try:
        extracted = trafilatura.extract(response.text)
    except Exception as exc:  # trafilatura can raise a range of parser errors
        print(f"[WARN] Could not extract article text: {exc}")
        return None

    if not extracted:
        return None

    words = extracted.split()
    if len(words) > MAX_ARTICLE_WORDS:
        words = words[:MAX_ARTICLE_WORDS]
    return " ".join(words)


def parse_topic_and_summary(raw_text):
    """
    Pulls "Topic: ..." and "Summary: ..." out of Groq's reply. If the
    model didn't follow the format for some reason, falls back to using
    the whole reply as the summary with no separate topic line.
    """
    topic_match = re.search(r"Topic:\s*(.+)", raw_text)
    summary_match = re.search(r"Summary:\s*(.+)", raw_text, re.DOTALL)

    topic = clean_text(topic_match.group(1)) if topic_match else None
    summary_text = (
        clean_text(summary_match.group(1)) if summary_match else clean_text(raw_text)
    )
    return topic, summary_text


def paraphrase_with_groq(title, summary, category, article_text=None):
    """
    Asks Groq for a short topic line plus an original summary (no outlet
    name, no links) — using the full fetched article text when available,
    falling back to the RSS snippet otherwise. Returns a (topic, summary)
    tuple, or None (never raises) if GROQ_API_KEY isn't set or the call
    fails for any reason, so callers can fall back to the cleaned-text
    version without the run ever failing over this.
    """
    if not GROQ_API_KEY:
        return None

    content_for_prompt = article_text or summary or "No further details available."

    prompt = (
        "You are given a Kenyan news story. Produce two things, entirely "
        "in your own words:\n"
        "1. A short topic line (under 12 words) capturing what the story "
        "is about.\n"
        f"2. An original summary, no more than {MAX_SUMMARY_WORDS} words, "
        "factual and neutral. Do not name the publication or outlet, do "
        "not include any links or URLs, and do not closely mirror the "
        "original phrasing or sentence structure. If there isn't much "
        "detail to work with, a shorter summary is fine.\n\n"
        "Respond in exactly this format, nothing else:\n"
        "Topic: <topic line>\n"
        "Summary: <summary text>\n\n"
        f"Category: {category}\n"
        f"Title: {title}\n"
        f"Article text:\n{content_for_prompt}"
    )

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 400,
            },
            timeout=GROQ_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
        raw_text = data["choices"][0]["message"]["content"]
        if not raw_text or not raw_text.strip():
            return None
        return parse_topic_and_summary(raw_text)
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        print(f"[WARN] Groq paraphrase failed, using cleaned-text fallback: {exc}")
        return None


def cleaned_fallback_body(title, summary):
    """
    Zero-dependency fallback used when Groq isn't configured or fails:
    just the title and snippet cleaned up, no outlet attribution line,
    no link text.
    """
    clean_title = title.rsplit(" - ", 1)[0].strip() if " - " in title else title
    body = clean_title if clean_title.endswith((".", "!", "?")) else f"{clean_title}."
    if summary:
        body += f" {summary}"
    return body


def make_facebook_post(title, summary, link, category):
    """
    Builds the Facebook post text as a short topic line + paraphrased
    summary of the story — no outlet attribution line, no link text in
    the body. Fetches up to MAX_ARTICLE_WORDS of the full article first
    (falling back to the RSS snippet if that fails), then asks Groq to
    summarize it. If Groq isn't configured or fails, falls back to a
    cleaned reformat of the title/snippet. The original article link is
    still passed to Facebook separately (as the post's link-preview
    target), so the source stays reachable via the auto-generated
    preview card even though it's not printed here.
    """
    article_text = fetch_article_text(link)
    groq_result = paraphrase_with_groq(title, summary, category, article_text)

    if groq_result:
        topic, body_summary = groq_result
        body = f"{topic}\n\n{body_summary}" if topic else body_summary
    else:
        body = cleaned_fallback_body(title, summary)

    return f"📰 Kenya Update\n\n{body}"


def facebook_endpoint():
    if FACEBOOK_GRAPH_VERSION:
        return (
            f"https://graph.facebook.com/"
            f"{FACEBOOK_GRAPH_VERSION}/"
            f"{FACEBOOK_PAGE_ID}/feed"
        )
    return f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}/feed"


def post_to_facebook(message, link):
    if not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN:
        return False, "Facebook credentials are missing."

    payload = {
        "message": message,
        "link": link,
        "access_token": FACEBOOK_PAGE_ACCESS_TOKEN,
    }

    try:
        response = requests.post(
            facebook_endpoint(),
            data=payload,
            timeout=FACEBOOK_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        return False, f"Network error: {exc}"

    try:
        result = response.json()
    except ValueError:
        result = {"raw": response.text}

    if response.ok and result.get("id"):
        return True, str(result["id"])

    return False, json.dumps(result, ensure_ascii=False)


def append_suggestions(entries):
    exists = os.path.exists(SUGGESTED_POSTS_FILE)

    with open(SUGGESTED_POSTS_FILE, "a", encoding="utf-8") as f:
        if not exists:
            f.write("# Suggested Posts\n\n")

        f.write(
            f"## Scan run: {nairobi_timestamp()} (Nairobi time)\n\n"
        )

        for entry in entries:
            flag = (
                " ⚠️ REVIEW — possibly sensitive content"
                if entry["flagged"] else ""
            )

            f.write(
                f"- **[{entry['category']}]** "
                f"{entry['title']}{flag}\n"
            )
            f.write(f"  - Source: {entry['source']}\n")
            f.write(f"  - Link: {entry['link']}\n")
            f.write(
                f"  - Facebook: "
                f"{entry.get('facebook_status', 'not attempted')}\n\n"
            )


def main():
    print("=" * 70)
    print("KENYA NEWS SCANNER + FACEBOOK PUBLISHER")
    print("=" * 70)
    print(f"Scan time: {nairobi_timestamp()} (Nairobi)")
    print(f"RSS feeds configured: {len(FEEDS)}")
    print(f"Lookback: {LOOKBACK_HOURS} hours")
    print(f"Maximum suggestions: {MAX_SUGGESTIONS_PER_RUN}")
    print(f"Maximum Facebook posts: {MAX_FACEBOOK_POSTS_PER_RUN}")
    print(
        f"Groq paraphrasing (full-article, topic + <{MAX_SUMMARY_WORDS}w summary): "
        f"{'enabled' if GROQ_API_KEY else 'disabled (using cleaned-text fallback)'}"
    )

    # Posting is required, not optional — fail the run loudly and early
    # if credentials are missing instead of silently downgrading to
    # suggestions-only, so a misconfigured secret is never mistaken for
    # "everything's fine, just no posts today."
    facebook_enabled = AUTO_POST_TO_FACEBOOK
    if facebook_enabled and (not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN):
        raise SystemExit(
            "[FATAL] AUTO_POST_TO_FACEBOOK is True but FACEBOOK_PAGE_ID / "
            "FACEBOOK_PAGE_ACCESS_TOKEN are not set. Check the repository "
            "secrets — refusing to run in suggestions-only mode."
        )

    print(f"Facebook auto-post: {facebook_enabled}")
    print("=" * 70)

    seen_titles = load_json_set(POSTED_LOG_FILE, quarantine_suspicious=True)
    facebook_log = load_json_set(FACEBOOK_POST_LOG_FILE, quarantine_suspicious=True)

    new_entries = []
    suggestions_made = 0
    facebook_posts_made = 0
    feeds_checked = 0
    feeds_failed = 0

    for feed_source in FEEDS:
        if suggestions_made >= MAX_SUGGESTIONS_PER_RUN:
            break

        category = feed_source["category"]
        feed_url = feed_source["url"]

        print(f"[CHECK] {category}")

        try:
            feed = parse_feed_safely(feed_url)
            feeds_checked += 1
        except Exception as exc:
            feeds_failed += 1
            print(f"[WARN] {category}: {exc}")
            continue

        if getattr(feed, "bozo", False) and not feed.entries:
            feeds_failed += 1
            bozo_exc = getattr(feed, "bozo_exception", None)
            print(f"[WARN] No usable entries: {feed_url} ({bozo_exc})")
            continue

        if not feed.entries:
            print(f"[INFO] {category}: no entries")
            continue

        print(f"[OK] {category}: {len(feed.entries)} entries")

        for entry in feed.entries:
            if suggestions_made >= MAX_SUGGESTIONS_PER_RUN:
                break

            title = clean_text(entry.get("title", ""))
            link = clean_text(entry.get("link", ""))
            summary = strip_html(entry.get("summary", ""))

            published_parsed = (
                entry.get("published_parsed")
                or entry.get("updated_parsed")
            )

            if not title or not link:
                continue

            key = normalize_title(title)

            if key in seen_titles:
                continue

            if not is_recent(published_parsed):
                continue

            flagged = is_sensitive(title, summary)

            source = source_name_from_title(title, category)

            entry_data = {
                "category": category,
                "title": title,
                "link": link,
                "source": source,
                "flagged": flagged,
                "facebook_status": "not attempted",
            }

            # Sensitive stories are saved for manual review but are NOT
            # automatically published.
            if flagged:
                entry_data["facebook_status"] = "held for manual review"
                new_entries.append(entry_data)
                seen_titles.add(key)
                suggestions_made += 1
                continue

            if facebook_enabled:
                if facebook_posts_made >= MAX_FACEBOOK_POSTS_PER_RUN:
                    entry_data["facebook_status"] = "queued; Facebook limit reached"
                elif key in facebook_log:
                    entry_data["facebook_status"] = "already posted"
                else:
                    message = make_facebook_post(
                        title,
                        summary,
                        link,
                        category,
                    )

                    ok, result = post_to_facebook(
                        message,
                        link,
                    )

                    if ok:
                        facebook_posts_made += 1
                        facebook_log.add(key)
                        entry_data["facebook_status"] = (
                            f"POSTED ({result})"
                        )
                        print(
                            f"[FACEBOOK] Posted: {title}"
                        )
                    else:
                        entry_data["facebook_status"] = (
                            f"FAILED: {result}"
                        )
                        print(
                            f"[FACEBOOK ERROR] {category}: {result}"
                        )

                    # Small delay to reduce burst posting.
                    time.sleep(1)

            new_entries.append(entry_data)
            seen_titles.add(key)
            suggestions_made += 1

    if new_entries:
        append_suggestions(new_entries)
        save_json_set(POSTED_LOG_FILE, seen_titles)
        save_json_set(FACEBOOK_POST_LOG_FILE, facebook_log)

    print()
    print("=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)
    print(f"New stories: {len(new_entries)}")
    print(f"Facebook posts made: {facebook_posts_made}")
    print(f"Feeds checked: {feeds_checked}")
    print(f"Feed failures: {feeds_failed}")
    print(f"Output: {SUGGESTED_POSTS_FILE}")
    print(f"Dedup log: {POSTED_LOG_FILE}")
    print(f"Facebook log: {FACEBOOK_POST_LOG_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()
