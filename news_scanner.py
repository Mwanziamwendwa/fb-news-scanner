"""
Kenya News Scanner + Facebook Page Publisher

Reads RSS feeds, finds recent stories, deduplicates them (both against
already-seen/posted stories and against other feeds reporting the same
story in this same scan), flags sensitive content, resolves the real
article URL (so Facebook's link preview shows the actual publisher
page instead of a Google News interstitial), fetches up to ~1000 words
of the full article, and builds a short topic + under-200-word summary
using a local, whole-sentence extractive summarizer (no external
paraphrasing API is used).

Rather than posting only whatever it finds in a single run and dropping
the rest, new stories are added to a persistent queue (post_queue.json)
and the script posts up to MAX_FACEBOOK_POSTS_PER_RUN from the FRONT of
that queue each run (oldest-found first), so a busy scan's extra stories
carry over and get posted on later runs instead of being lost.

Runs automatically every 3 hours (see the workflow's cron) and can
also be triggered manually at any time from the Actions tab.
LOOKBACK_HOURS (4) covers both cases safely — dedup via
posted_log.json prevents duplicate posts if a manual run overlaps
a scheduled one.

IMPORTANT:
- Never put your Facebook token in this file.
- Use GitHub Secrets:
    FACEBOOK_PAGE_ID
    FACEBOOK_PAGE_ACCESS_TOKEN
- The token previously pasted into chat should be revoked/rotated.
- Posts never include an outlet-attribution line, a "Kenya Update" /
  category header, or link text in the body. The post's title is
  never used as-is either — Google News "full coverage" entries
  sometimes repeat the same headline twice back to back (e.g.
  "X. X"), so it's deduplicated before it's used anywhere.
- The RSS summary from Google News topic feeds can bundle several
  outlets' headlines into one blob (a "full coverage" cluster) instead
  of describing a single story. That text is never posted verbatim:
  the post body prefers a whole-sentence extractive summary built from
  the real fetched article page over the raw RSS snippet, and if
  neither the article nor a clean snippet is usable, the story is held
  and retried later rather than posted with thin or junky text.
- Posts go out as plain text status updates — no link is attached to
  the Facebook post at all, so no link-preview card ever shows a
  source domain (publisher or otherwise). The real article URL is
  still resolved and fetched internally (to build the summary content)
  and is recorded in suggested_posts.md for your own reference, but it
  is never sent to Facebook.
- Every run rotates which feed it starts scanning from
  (feed_rotation_state.json), so with 62 feeds and a per-run cap on
  new stories, every feed gets fair coverage over time instead of the
  same first few always winning.
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
POSTED_LOG_FILE = "posted_log.json"          # everything ever found (posted, queued, or held) — used for dedup
FACEBOOK_POST_LOG_FILE = "facebook_post_log.json"  # only stories actually posted to Facebook
POST_QUEUE_FILE = "post_queue.json"          # stories found but not yet posted, oldest-first
FEED_ROTATION_STATE_FILE = "feed_rotation_state.json"  # which feed to start scanning from next run

# The workflow runs automatically every 3 hours and can also be
# triggered manually at any time. This window is set to 4 hours —
# matching the 3-hour gap plus a 1-hour buffer, so a scheduled run
# that's delayed (cron start times aren't guaranteed to the minute)
# still catches everything, and a manual run in between scheduled
# ones is always safe too: dedup against posted_log.json means
# running more often than the window never causes duplicate posts.
LOOKBACK_HOURS = 4
# These caps limit how much a single manual run does — how many new
# candidate stories it gathers, and how many it actually posts to
# Facebook before stopping (anything left over carries over in
# post_queue.json for the next time you press Run).
MAX_SUGGESTIONS_PER_RUN = 45          # cap on NEW stories gathered per run (before posting)
MAX_FACEBOOK_POSTS_PER_RUN = 12       # cap on posts actually published per run
MAX_POST_ATTEMPTS = 3                 # give up on a queued story after this many failed post attempts
QUEUE_MAX_SIZE = 300                  # safety cap so the backlog can't grow unbounded
LOG_RETENTION_DAYS = 14               # how long a "seen" story is remembered for dedup purposes
FEED_TIMEOUT_SECONDS = 15
FACEBOOK_TIMEOUT_SECONDS = 30
ARTICLE_FETCH_TIMEOUT_SECONDS = 15

# Full article text is trimmed to this many words before being stored
# or summarized.
MAX_ARTICLE_WORDS = 1000

# Target maximum length for the extractive summary. Can be shorter
# depending on how much material is available.
MAX_SUMMARY_WORDS = 200

# How similar two titles' significant keywords need to be (as a fraction
# of the smaller title's keyword count) to be treated as the same story
# reported by different outlets. This is a free, local heuristic (no
# extra API calls) — it catches clear overlaps, not every rewording.
TITLE_SIMILARITY_THRESHOLD = 0.45

# Set to false if you want suggestions only.
AUTO_POST_TO_FACEBOOK = True

FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "").strip()
FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()

# Optional. If omitted, the script uses the unversioned Graph endpoint.
FACEBOOK_GRAPH_VERSION = os.getenv("FACEBOOK_GRAPH_VERSION", "").strip()

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

# Entries that look like this (underscore-separated, no spaces, no
# punctuation) are almost certainly category/taxonomy keys from some
# other process, not real RSS titles. normalize_title() never produces
# this shape from a real headline. We quarantine them on load so they
# can't silently block or corrupt future dedup.
_SUSPICIOUS_SLUG_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)+$")

# Google News "topic" feeds sometimes bundle several unrelated headlines
# and outlet names into one entry's summary (a "full coverage" style
# cluster) instead of describing a single story — e.g. "Headline A
# example.co.ke Headline B other-site.com ... See less". Detected
# heuristically by 2+ outlet-domain-looking tokens, which a normal
# single-story snippet won't contain.
_SOURCE_DOMAIN_RE = re.compile(r"\b[\w-]+\.(?:co\.ke|com|org|net|co)\b", re.IGNORECASE)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_CANONICAL_LINK_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OG_URL_RE = re.compile(
    r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "this", "that", "these", "those",
    "he", "she", "they", "we", "you", "i", "his", "her", "their", "our",
    "your", "my", "will", "would", "can", "could", "should", "has",
    "have", "had", "not", "no", "after", "before", "over", "into",
    "about", "amid", "amidst", "kenya", "kenyan", "kenyans", "news",
    "latest", "update", "updates", "says", "say", "said",
}

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


def dedupe_title(title):
    """
    Google News 'full coverage' entries sometimes repeat the exact same
    headline twice back-to-back, e.g. "X happened. X happened" — this
    collapses that down to a single clean copy so the duplicate never
    ends up in the post body. If the title isn't duplicated, it's
    returned unchanged (just whitespace-cleaned).
    """
    t = clean_text(title)
    if ". " in t:
        first, rest = t.split(". ", 1)
        if first.strip().rstrip(". ").lower() == rest.strip().rstrip(". ").lower():
            return first.strip()
    return t


def title_keywords(title):
    words = re.findall(r"[a-z0-9']+", title.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def titles_are_similar(title_a, title_b, threshold=TITLE_SIMILARITY_THRESHOLD):
    """
    Rough cross-outlet duplicate check: compares the significant
    (non-stopword) keywords of two titles. Different outlets often
    phrase the same story very differently, so this is a heuristic,
    not a guarantee — it catches clear overlaps, not every rewording.
    """
    kw_a = title_keywords(title_a)
    kw_b = title_keywords(title_b)
    if not kw_a or not kw_b:
        return False
    overlap = kw_a & kw_b
    smaller = min(len(kw_a), len(kw_b))
    return (len(overlap) / smaller) >= threshold


def find_similar_title(title, candidate_titles):
    """Returns the first candidate title considered a likely duplicate, or None."""
    for candidate in candidate_titles:
        if titles_are_similar(title, candidate):
            return candidate
    return None


def is_full_coverage_cluster(summary):
    """
    True if the summary looks like a Google News 'full coverage' style
    cluster (several outlet-domain tokens bundled together) rather than
    a normal single-story snippet.
    """
    return len(_SOURCE_DOMAIN_RE.findall(summary or "")) >= 2


def clean_summary_for_use(summary):
    """
    Drops the summary entirely if it looks like a multi-story cluster
    dump rather than a real snippet about one story — that text is
    confusing (and was showing up verbatim in posts) rather than useful
    as summarizing material.
    """
    if is_full_coverage_cluster(summary):
        return ""
    return summary


def load_json_dict(filename):
    """
    Loads a JSON object of {title: iso_timestamp}. Transparently
    migrates an old flat-list format (title strings only, no
    timestamps) by stamping every entry with the current time, so
    existing log files from before this change keep working.
    """
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        print(f"[WARN] Could not read {filename}; starting fresh.")
        return {}

    now_iso = datetime.now(pytz.utc).isoformat()

    if isinstance(data, list):
        return {title: now_iso for title in data}
    if isinstance(data, dict):
        return data

    print(f"[WARN] Unexpected structure in {filename}; starting fresh.")
    return {}


def save_json_dict(filename, values):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(values, f, indent=2, ensure_ascii=False, sort_keys=True)


def quarantine_suspicious(log_dict, filename):
    suspicious = {k for k in log_dict if _SUSPICIOUS_SLUG_RE.match(k)}
    if suspicious:
        print(
            f"[WARN] {filename}: ignoring {len(suspicious)} entries that "
            f"look like category slugs, not article titles: {sorted(suspicious)}"
        )
        for k in suspicious:
            log_dict.pop(k, None)
    return log_dict


def prune_old_entries(log_dict, retention_days=LOG_RETENTION_DAYS):
    cutoff = datetime.now(pytz.utc) - timedelta(days=retention_days)
    kept = {}
    for title, timestamp in log_dict.items():
        try:
            when = datetime.fromisoformat(timestamp)
        except (TypeError, ValueError):
            kept[title] = timestamp
            continue
        if when >= cutoff:
            kept[title] = timestamp
    return kept


def load_queue():
    if not os.path.exists(POST_QUEUE_FILE):
        return []
    try:
        with open(POST_QUEUE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        print(f"[WARN] Could not read {POST_QUEUE_FILE}; starting fresh.")
    return []


def save_queue(queue):
    with open(POST_QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(queue, f, indent=2, ensure_ascii=False)


def load_feed_rotation_offset():
    """
    Each run stops scanning once it collects MAX_SUGGESTIONS_PER_RUN
    new candidates, which — with a fixed feed order — means whichever
    feeds are first in FEEDS get scanned every run while feeds further
    down the list may rarely (or never) get reached. This offset
    rotates which feed the scan starts from each run, so coverage is
    shared fairly across all configured feeds over time instead of
    always favoring the same ones. Defaults to 0 (start of the list)
    if no state file exists yet or it can't be read.
    """
    if not os.path.exists(FEED_ROTATION_STATE_FILE):
        return 0
    try:
        with open(FEED_ROTATION_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("offset", 0))
    except (OSError, json.JSONDecodeError, ValueError, TypeError, AttributeError):
        print(f"[WARN] Could not read {FEED_ROTATION_STATE_FILE}; starting from feed 0.")
        return 0


def save_feed_rotation_offset(offset):
    with open(FEED_ROTATION_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"offset": offset}, f, indent=2, ensure_ascii=False)


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


def resolve_article_url(link):
    """
    Google News RSS links point at a news.google.com interstitial page
    rather than the publisher's actual article. If that URL is posted
    to Facebook as-is, Facebook's link-preview scraper renders the
    Google News interstitial (the "popup") instead of the real story.

    This follows real HTTP redirects first, and if the final URL is
    still on news.google.com, tries to pull the true article URL out
    of that page's canonical/og:url meta tags. Falls back to the
    original link (and never raises) if none of that works, so a
    resolution failure never breaks the run — it just means that one
    story's preview may point at Google News instead of the source.

    Returns (resolved_url, page_html) — page_html is passed straight
    into fetch_article_text so we don't fetch the same page twice.
    """
    try:
        response = requests.get(
            link,
            headers=FEED_REQUEST_HEADERS,
            timeout=ARTICLE_FETCH_TIMEOUT_SECONDS,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        print(f"[WARN] Could not resolve article URL: {exc}")
        return link, None

    final_url = response.url
    page_html = response.text if response.ok else None

    if "news.google.com" in final_url and page_html:
        match = _CANONICAL_LINK_RE.search(page_html) or _OG_URL_RE.search(page_html)
        if match:
            candidate = html.unescape(match.group(1)).strip()
            if candidate.startswith("http") and "news.google.com" not in candidate:
                return candidate, page_html

    return final_url, page_html


def fetch_article_text(link, page_html=None):
    """
    Fetches (or reuses an already-fetched) publisher article page and
    extracts up to MAX_ARTICLE_WORDS words of the main body text via
    trafilatura. Returns None (never raises) if the fetch or extraction
    fails for any reason — sites that block scraping, paywall, time
    out, or have layouts trafilatura can't parse. Callers fall back to
    the RSS snippet, or hold the story, in that case.
    """
    if page_html is None:
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
        page_html = response.text

    try:
        extracted = trafilatura.extract(page_html)
    except Exception as exc:  # trafilatura can raise a range of parser errors
        print(f"[WARN] Could not extract article text: {exc}")
        return None

    if not extracted:
        return None

    words = extracted.split()
    if len(words) > MAX_ARTICLE_WORDS:
        words = words[:MAX_ARTICLE_WORDS]
    return " ".join(words)


def extractive_summary(text, max_words=MAX_SUMMARY_WORDS):
    """
    Whole-sentence extractive summary: takes complete sentences from
    the front of the real fetched article, in order, up to max_words.
    Never cuts a sentence off mid-way.
    """
    if not text:
        return ""
    sentences = _SENTENCE_SPLIT_RE.split(text.strip())
    word_count = 0
    kept = []
    for sentence in sentences:
        words = sentence.split()
        if not words:
            continue
        if kept and word_count + len(words) > max_words:
            break
        kept.append(sentence)
        word_count += len(words)
        if word_count >= max_words:
            break
    return " ".join(kept)


def build_fallback_body(title, summary, article_text):
    """
    Prefers a whole-sentence extractive summary pulled from the real
    fetched article (a single source) over the raw RSS snippet, since
    Google News' aggregator snippets are prone to bundling several
    outlets' headlines together even after clean_summary_for_use()
    filters the obvious cases. Returns None if there isn't enough real
    content to build a decent post from, so the caller holds the story
    for a later retry instead of publishing something thin or junky.
    """
    intro = title if title.endswith((".", "!", "?")) else f"{title}."

    if article_text:
        extract = extractive_summary(article_text)
        if extract:
            return f"{intro}\n\n{extract}"

    if summary:
        return f"{intro} {summary}"

    return None


def make_facebook_post(title, summary, link, category):
    """
    Builds the Facebook post text as a title + extractive summary of
    the story — no outlet attribution, no category/"Kenya Update"
    header, no link text or URL in the body (the post is published as
    a plain text status update with no link attached at all — see
    post_to_facebook). Resolves the real article URL first (purely to
    fetch the actual article text, not a Google News interstitial),
    fetches up to MAX_ARTICLE_WORDS of the full article, then builds a
    whole-sentence extractive summary of it (or, failing that, the
    cleaned RSS snippet).

    Returns (message, resolved_link). resolved_link is not posted to
    Facebook — it's only used for the suggested_posts.md log so you
    have a reference back to the source story. message is None if
    there wasn't enough real content to build a decent post — callers
    should treat that as a failed attempt and retry later rather than
    post it.
    """
    resolved_link, page_html = resolve_article_url(link)
    article_text = fetch_article_text(resolved_link, page_html=page_html)
    clean_title = dedupe_title(title)
    cleaned_summary = clean_summary_for_use(summary)

    message = build_fallback_body(clean_title, cleaned_summary, article_text)
    return message, resolved_link


def facebook_endpoint():
    if FACEBOOK_GRAPH_VERSION:
        return (
            f"https://graph.facebook.com/"
            f"{FACEBOOK_GRAPH_VERSION}/"
            f"{FACEBOOK_PAGE_ID}/feed"
        )
    return f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}/feed"


def post_to_facebook(message):
    """
    Posts as a plain text status update — no link parameter at all.
    Attaching a link makes Facebook render a link-preview card that
    shows the source domain (whether that's the resolved publisher
    site or, when resolution fails, a bare "news.google.com" card),
    which is exactly the "shows where this came from" problem. Posting
    message-only avoids that entirely.
    """
    if not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN:
        return False, "Facebook credentials are missing."

    payload = {
        "message": message,
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
    if not entries:
        return

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
                if entry.get("flagged") else ""
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
    print(f"Maximum new suggestions per run: {MAX_SUGGESTIONS_PER_RUN}")
    print(f"Maximum Facebook posts per run: {MAX_FACEBOOK_POSTS_PER_RUN}")
    print(
        f"Summary method: local whole-sentence extractive summary "
        f"(max {MAX_SUMMARY_WORDS} words), no external paraphrasing API"
    )

    facebook_enabled = AUTO_POST_TO_FACEBOOK
    if facebook_enabled and (not FACEBOOK_PAGE_ID or not FACEBOOK_PAGE_ACCESS_TOKEN):
        raise SystemExit(
            "[FATAL] AUTO_POST_TO_FACEBOOK is True but FACEBOOK_PAGE_ID / "
            "FACEBOOK_PAGE_ACCESS_TOKEN are not set. Check the repository "
            "secrets — refusing to run in suggestions-only mode."
        )

    print(f"Facebook auto-post: {facebook_enabled}")
    print("=" * 70)

    now_iso = datetime.now(pytz.utc).isoformat()

    posted_log = prune_old_entries(
        quarantine_suspicious(load_json_dict(POSTED_LOG_FILE), POSTED_LOG_FILE)
    )
    facebook_log = prune_old_entries(
        quarantine_suspicious(load_json_dict(FACEBOOK_POST_LOG_FILE), FACEBOOK_POST_LOG_FILE)
    )
    queue = load_queue()
    queued_titles = [item["title"] for item in queue]

    # ---- Phase 1: scan feeds, collect new (deduplicated) candidate stories ----
    feed_rotation_offset = load_feed_rotation_offset() % len(FEEDS)
    ordered_feeds = FEEDS[feed_rotation_offset:] + FEEDS[:feed_rotation_offset]
    print(f"Feed scan starting at index {feed_rotation_offset} ({ordered_feeds[0]['category']}) — rotates each run for fair coverage")

    candidates = []
    held_for_review = []
    suggestions_made = 0
    feeds_checked = 0
    feeds_failed = 0
    feeds_attempted_this_run = 0

    for feed_source in ordered_feeds:
        if suggestions_made >= MAX_SUGGESTIONS_PER_RUN:
            break

        feeds_attempted_this_run += 1
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

            raw_title = clean_text(entry.get("title", ""))
            title = dedupe_title(raw_title)
            link = clean_text(entry.get("link", ""))
            raw_summary = strip_html(entry.get("summary", ""))
            summary = clean_summary_for_use(raw_summary)

            published_parsed = (
                entry.get("published_parsed")
                or entry.get("updated_parsed")
            )

            if not title or not link:
                continue

            key = normalize_title(title)

            # Exact or fuzzy duplicate of something already posted, held,
            # or previously queued — including a late report of a story
            # another outlet already broke, which gets auto-declined here.
            if key in posted_log or find_similar_title(title, posted_log.keys()):
                continue

            if key in queued_titles or find_similar_title(title, queued_titles):
                continue

            # Duplicate of a story another feed already surfaced earlier
            # in this same scan.
            if find_similar_title(title, [c["title"] for c in candidates]):
                continue

            if not is_recent(published_parsed):
                continue

            flagged = is_sensitive(title, raw_summary)
            source = source_name_from_title(raw_title, category)

            if flagged:
                held_for_review.append({
                    "category": category,
                    "title": title,
                    "link": link,
                    "source": source,
                    "flagged": True,
                    "facebook_status": "held for manual review",
                })
                posted_log[key] = now_iso
                suggestions_made += 1
                continue

            candidates.append({
                "title": title,
                "normalized_title": key,
                "link": link,
                "source": source,
                "category": category,
                "summary": summary,
                "discovered_at": now_iso,
                "attempts": 0,
            })
            posted_log[key] = now_iso
            queued_titles.append(title)
            suggestions_made += 1

    if len(queue) + len(candidates) > QUEUE_MAX_SIZE:
        room = max(0, QUEUE_MAX_SIZE - len(queue))
        if room < len(candidates):
            print(
                f"[WARN] Queue at capacity ({QUEUE_MAX_SIZE}); dropping "
                f"{len(candidates) - room} newly found stories this run."
            )
        candidates = candidates[:room]

    queue.extend(candidates)

    new_feed_rotation_offset = (feed_rotation_offset + feeds_attempted_this_run) % len(FEEDS)
    save_feed_rotation_offset(new_feed_rotation_offset)

    # ---- Phase 2: post up to MAX_FACEBOOK_POSTS_PER_RUN from the FRONT of the queue ----
    facebook_posts_made = 0
    posted_entries = []
    failed_entries = []
    remaining_queue = []

    for item in queue:
        if not facebook_enabled or facebook_posts_made >= MAX_FACEBOOK_POSTS_PER_RUN:
            remaining_queue.append(item)
            continue

        message, resolved_link = make_facebook_post(
            item["title"], item["summary"], item["link"], item["category"]
        )

        if message is None:
            item["attempts"] = item.get("attempts", 0) + 1
            reason = "no usable article text or snippet to build a post from"
            if item["attempts"] >= MAX_POST_ATTEMPTS:
                failed_entries.append({
                    "category": item["category"],
                    "title": item["title"],
                    "link": item["link"],
                    "source": item["source"],
                    "flagged": False,
                    "facebook_status": f"FAILED permanently after {MAX_POST_ATTEMPTS} attempts: {reason}",
                })
                print(f"[FACEBOOK] Giving up on: {item['title']} ({reason})")
            else:
                remaining_queue.append(item)
                print(
                    f"[FACEBOOK] Will retry "
                    f"({item['attempts']}/{MAX_POST_ATTEMPTS}): {item['title']} ({reason})"
                )
            time.sleep(1)
            continue

        ok, result = post_to_facebook(message)

        if ok:
            facebook_posts_made += 1
            facebook_log[item["normalized_title"]] = now_iso
            posted_entries.append({
                "category": item["category"],
                "title": item["title"],
                "link": resolved_link,
                "source": item["source"],
                "flagged": False,
                "facebook_status": f"POSTED ({result})",
            })
            print(f"[FACEBOOK] Posted: {item['title']}")
        else:
            item["attempts"] = item.get("attempts", 0) + 1
            if item["attempts"] >= MAX_POST_ATTEMPTS:
                failed_entries.append({
                    "category": item["category"],
                    "title": item["title"],
                    "link": item["link"],
                    "source": item["source"],
                    "flagged": False,
                    "facebook_status": f"FAILED permanently after {MAX_POST_ATTEMPTS} attempts: {result}",
                })
                print(f"[FACEBOOK ERROR] Giving up on: {item['title']} ({result})")
            else:
                remaining_queue.append(item)
                print(
                    f"[FACEBOOK ERROR] Will retry "
                    f"({item['attempts']}/{MAX_POST_ATTEMPTS}): {item['title']} ({result})"
                )

        # Small delay to reduce burst posting.
        time.sleep(1)

    queue = remaining_queue

    posted_or_failed_keys = {normalize_title(e["title"]) for e in posted_entries + failed_entries}
    still_queued_entries = [
        {
            "category": c["category"],
            "title": c["title"],
            "link": c["link"],
            "source": c["source"],
            "flagged": False,
            "facebook_status": "queued for a future run",
        }
        for c in candidates
        if c["normalized_title"] not in posted_or_failed_keys
    ]

    all_new_entries = held_for_review + posted_entries + failed_entries + still_queued_entries
    append_suggestions(all_new_entries)

    save_json_dict(POSTED_LOG_FILE, posted_log)
    save_json_dict(FACEBOOK_POST_LOG_FILE, facebook_log)
    save_queue(queue)

    print()
    print("=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)
    print(f"New stories found this run: {len(candidates)}")
    print(f"Facebook posts made this run: {facebook_posts_made}")
    print(f"Stories remaining in queue: {len(queue)}")
    print(f"Held for manual review: {len(held_for_review)}")
    print(f"Feeds checked: {feeds_checked}")
    print(f"Feed failures: {feeds_failed}")
    print(f"Feeds attempted this run: {feeds_attempted_this_run} of {len(FEEDS)} (next run starts at index {new_feed_rotation_offset})")
    print(f"Output: {SUGGESTED_POSTS_FILE}")
    print(f"Seen/dedup log: {POSTED_LOG_FILE}")
    print(f"Facebook log: {FACEBOOK_POST_LOG_FILE}")
    print(f"Post queue: {POST_QUEUE_FILE}")
    print(f"Feed rotation state: {FEED_ROTATION_STATE_FILE}")
    print("=" * 70)


if __name__ == "__main__":
    main()
