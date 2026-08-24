import os
import json
import html
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import feedparser
import pytz
import requests
from bs4 import BeautifulSoup

# Local Kenyan timezone synchronization
NAIROBI_TZ = pytz.timezone("Africa/Nairobi")

SUGGESTED_POSTS_FILE = "suggested_posts.md"
SEEN_LOG_FILE = "seen_log.json"
FEED_HEALTH_FILE = "feed_health.json"

# File used by the Dynamic Agent to track exactly when your last manual scan completed
LAST_RUN_STATE_FILE = "last_run_state.json"

# File used by the Self-Healing Agent to remember feed fixes across manual runs,
# so a feed that was repaired last time doesn't have to be re-discovered every run.
FEED_OVERRIDES_FILE = "feed_overrides.json"

MAX_SUGGESTIONS_PER_RUN = 100
LOG_RETENTION_DAYS = 14
FEED_TIMEOUT_SECONDS = 15
ARTICLE_FETCH_TIMEOUT_SECONDS = 15
FEED_HEALTH_CHECK_TIMEOUT_SECONDS = 10

TITLE_SIMILARITY_THRESHOLD = 0.45

# Post-body summary length bound (word count). No minimum on purpose --
# a story is never dropped just because its article page only yielded
# a short amount of clean text. Only capped so a very long article
# doesn't turn into an unreadable wall of text in one Facebook post.
# Cut only at a full sentence boundary, never mid-sentence, never with
# an ellipsis.
SUMMARY_MAX_WORDS = 350

FEED_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )
}

SENSITIVE_TERMS = [
    "autopsy", "mutilated", "beheaded", "dismembered", "gore",
    "graphic images", "explicit video", "child abuse", "defiled",
    "gang rape", "mob justice", "lynched",
]

PAYWALL_AND_BIO_PATTERNS = [
    r"the standard group plc is a multi-media organization.*online services",
    r"the standard group is recognized as a leading multi-media house.*national and international interest",
    r"premium article.*ksh\d+/week",
    r"get full access",
    r"subscribe now for exclusive access",
    r"flash sale\s*!",
    r"offer ends in",
    r"subscribe now and enjoy \d+% off annual plans",
    r"uncover the stories others won't tell",
    r"already a subscriber",
    r"become a member to",
    r"your premium access has ended.*renew now",
    r"reclaim your full access.*renew\.",
]

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


def clean_and_strip_paywall_text(text):
    if not text:
        return ""
    cleaned_text = re.sub(r'<[^<]+?>', '', text)
    cleaned_text = html.unescape(cleaned_text)
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text)
    cleaned_text = cleaned_text.replace('\u2019', "'").replace('\u2018', "'")
    for pattern in PAYWALL_AND_BIO_PATTERNS:
        cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.IGNORECASE | re.DOTALL)
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    cleaned_text = re.sub(r'^[.,;:!?\s]+', '', cleaned_text)
    cleaned_text = re.sub(r'(?:\s*[.,;:!?]){2,}\s*$', '.', cleaned_text)
    return cleaned_text


def clean_and_tokenize(text):
    text = (text or "").lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    return [w for w in text.split() if w not in _STOPWORDS and len(w) > 1]


def get_title_similarity(t1, t2):
    tokens1 = set(clean_and_tokenize(t1))
    tokens2 = set(clean_and_tokenize(t2))
    if not tokens1 or not tokens2:
        return 0.0
    common = tokens1.intersection(tokens2)
    return len(common) / min(len(tokens1), len(tokens2))


def is_link_reachable(url):
    try:
        res = requests.head(url, headers=FEED_REQUEST_HEADERS, timeout=FEED_HEALTH_CHECK_TIMEOUT_SECONDS)
        if res.status_code < 400:
            return True
        with requests.get(
            url, headers=FEED_REQUEST_HEADERS,
            timeout=FEED_HEALTH_CHECK_TIMEOUT_SECONDS, stream=True
        ) as res:
            return res.status_code < 400
    except requests.RequestException:
        return False


_RSS_LINK_RE = re.compile(
    r'<link[^>]+type=["\'](?:application/rss\+xml|application/atom\+xml)["\'][^>]+href=["\']([^"\']+)["\']',
    re.IGNORECASE,
)


def _url_variants(url):
    variants = []
    seen = {url}

    def add(candidate):
        if candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)

    if url.startswith("https://"):
        add("http://" + url[len("https://"):])
    elif url.startswith("http://"):
        add("https://" + url[len("http://"):])

    if "://www." in url:
        add(url.replace("://www.", "://", 1))
    else:
        add(url.replace("://", "://www.", 1))

    if url.endswith("/"):
        add(url.rstrip("/"))
    else:
        add(url + "/")

    base = url.rstrip("/")
    if base.endswith("/feed"):
        root = base[: -len("/feed")]
        add(root + "/rss/")
        add(root + "/feed/")
    elif not base.endswith(("/feed", "/rss", ".xml", ".php")):
        add(base + "/feed/")
        add(base + "/rss/")

    return variants


def _autodiscover_feed_from_homepage(url):
    try:
        match = re.match(r"^(https?://[^/]+)", url)
        if not match:
            return None
        homepage = match.group(1) + "/"
        resp = requests.get(homepage, headers=FEED_REQUEST_HEADERS, timeout=FEED_HEALTH_CHECK_TIMEOUT_SECONDS)
        if not resp.ok:
            return None
        found = _RSS_LINK_RE.search(resp.text)
        if found:
            candidate = html.unescape(found.group(1)).strip()
            if candidate.startswith("http"):
                return candidate
    except Exception:
        pass
    return None


def _google_news_search_fallback(category):
    query = requests.utils.quote(f"Kenya {category}")
    return f"https://news.google.com/rss/search?q={query}&hl=en-KE&gl=KE&ceid=KE:en"


def _is_valid_feed(url):
    try:
        parsed = feedparser.parse(url)
        return bool(parsed.entries)
    except Exception:
        return False


def find_working_feed_url(original_url, category=None):
    for candidate in _url_variants(original_url):
        if is_link_reachable(candidate) and _is_valid_feed(candidate):
            return candidate, "url_variant"

    discovered = _autodiscover_feed_from_homepage(original_url)
    if (
        discovered
        and discovered != original_url
        and is_link_reachable(discovered)
        and _is_valid_feed(discovered)
    ):
        return discovered, "homepage_autodiscovery"

    if category:
        fallback = _google_news_search_fallback(category)
        if is_link_reachable(fallback) and _is_valid_feed(fallback):
            return fallback, "google_news_search_fallback"

    return None, None


def contains_sensitive_content(text):
    text_lower = (text or "").lower()
    for term in SENSITIVE_TERMS:
        if term in text_lower:
            return True
    return False


_NON_CONTENT_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "iframe", "noscript"]


def _extract_paragraphs(soup):
    for tag in soup.find_all(_NON_CONTENT_TAGS):
        tag.decompose()

    container = soup.find("article") or soup.find(attrs={"class": re.compile(r"(article|post)[-_]?(body|content)", re.I)})
    scope = container if container else soup

    paragraphs = [p.get_text(" ", strip=True) for p in scope.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p.split()) > 4]
    return " ".join(paragraphs)


def bound_section_by_words(text, max_words=SUMMARY_MAX_WORDS):
    words = text.split()
    if len(words) <= max_words:
        return text

    sentences = re.split(r'(?<=[.!?])\s+', text)
    section = ""
    word_count = 0
    for sentence in sentences:
        sentence_words = len(sentence.split())
        if word_count + sentence_words > max_words:
            break
        section += (" " if section else "") + sentence
        word_count += sentence_words

    if section:
        return section
    return " ".join(words[:max_words])


def _fetch_page(url):
    try:
        resp = requests.get(url, headers=FEED_REQUEST_HEADERS, timeout=ARTICLE_FETCH_TIMEOUT_SECONDS)
        if not resp.ok:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


def fetch_article_section(url):
    soup = _fetch_page(url)

    if soup is None:
        for candidate in _url_variants(url):
            soup = _fetch_page(candidate)
            if soup is not None:
                break

    if soup is None:
        return None

    raw_text = _extract_paragraphs(soup)
    cleaned = clean_and_strip_paywall_text(raw_text)
    if not cleaned:
        return None

    return bound_section_by_words(cleaned)


# ==============================================================================
# MAIN SCANNER ENGINE
# ==============================================================================
def main():
    print("[PIPELINE RUN] Starting Dynamic Multi-Agent News Scanner...")
    now_utc = datetime.now(pytz.utc)

    LOOKBACK_WINDOW_MINUTES = 24 * 60
    time_gap_minutes = LOOKBACK_WINDOW_MINUTES

    if os.path.exists(LAST_RUN_STATE_FILE):
        try:
            with open(LAST_RUN_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                last_run_time = datetime.fromisoformat(state["last_successful_run_utc"])
                print(f"[DYNAMIC AGENT] Last scan detected at: {last_run_time.astimezone(NAIROBI_TZ)}")
        except Exception:
            print("[DYNAMIC AGENT] State file unreadable.")

    print(f"[DYNAMIC AGENT] Lookback window fixed at: {time_gap_minutes} minutes (24 hours).")

    seen_log = []
    if os.path.exists(SEEN_LOG_FILE):
        try:
            with open(SEEN_LOG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                seen_log = loaded
            elif isinstance(loaded, dict):
                print("[WARN] seen_log.json is in an old dict format, migrating to list.")
                seen_log = list(loaded.keys())
            else:
                print("[WARN] seen_log.json has an unexpected structure, starting fresh.")
        except (OSError, json.JSONDecodeError, ValueError):
            print("[WARN] Could not read seen_log.json, starting fresh.")

    # Feed fixes remembered from previous manual runs: {category: working_url}.
    # This is what lets the Self-Healing Agent get smarter with each manual run
    # instead of re-discovering the same fix from scratch every time.
    feed_overrides = {}
    if os.path.exists(FEED_OVERRIDES_FILE):
        try:
            with open(FEED_OVERRIDES_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                feed_overrides = loaded
            else:
                print("[WARN] feed_overrides.json has an unexpected structure, starting fresh.")
        except (OSError, json.JSONDecodeError, ValueError):
            print("[WARN] Could not read feed_overrides.json, starting fresh.")

    if feed_overrides:
        print(f"[SELF-HEALING AGENT] Loaded {len(feed_overrides)} remembered feed fix(es) from previous run(s).")

    health_data = {}
    fresh_extracted_stories = []
    suggested_fixes = []
    overrides_changed = False

    def resolve_feed(feed):
        """
        Resolve a feed to a working URL.

        Order of attempts:
          1. A remembered override from a previous manual run, if one exists
             and still works -- this is the "learned" fast path, no repair
             chain needed.
          2. The original URL, if it's reachable as-is.
          3. The full repair chain (url variants -> homepage autodiscovery ->
             Google News fallback) when neither of the above works.

        Returns (feed, working_url, fix_method, is_new_fix) where is_new_fix
        tells the caller whether this fix was just discovered this run (and
        so needs to be persisted) or was reused from feed_overrides.json.
        """
        category = feed["category"]
        original_url = feed["url"]

        override_url = feed_overrides.get(category)
        if override_url and override_url != original_url:
            if is_link_reachable(override_url) and _is_valid_feed(override_url):
                return feed, override_url, "remembered_override", False
            print(f"[SELF-HEALING AGENT] Remembered fix for {category} stopped working, re-repairing...")

        working_url = original_url
        fix_method = None
        is_new_fix = False

        if not is_link_reachable(working_url):
            print(f"[SELF-HEALING AGENT] {category} unreachable, attempting repair...")
            fixed_url, fix_method = find_working_feed_url(original_url, category=category)
            if fixed_url:
                print(f"[SELF-HEALING AGENT] Fixed {category} via {fix_method}: {fixed_url}")
                working_url = fixed_url
                is_new_fix = True
            else:
                working_url = None

        return feed, working_url, fix_method, is_new_fix

    resolved = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(resolve_feed, feed) for feed in FEEDS]
        for future in as_completed(futures):
            resolved.append(future.result())

    resolved.sort(key=lambda r: FEEDS.index(r[0]))

    for feed, working_url, fix_method, is_new_fix in resolved:
        category = feed["category"]

        if working_url:
            health_data[category] = {
                "status": "ALIVE",
                "url_used": working_url,
                "auto_fixed": fix_method is not None,
                "fix_method": fix_method,
            }
            if fix_method:
                suggested_fixes.append({
                    "category": category,
                    "original_url": feed["url"],
                    "working_url": working_url,
                    "method": fix_method,
                    "reused": not is_new_fix,
                })

            # A repair that's actually different from the original URL gets
            # remembered so next manual run can use it straight away via
            # the "remembered_override" fast path above.
            if is_new_fix and working_url != feed["url"]:
                if feed_overrides.get(category) != working_url:
                    feed_overrides[category] = working_url
                    overrides_changed = True

            try:
                parsed = feedparser.parse(working_url)
                for entry in parsed.entries:
                    title = (getattr(entry, "title", "") or "").strip()
                    link = (getattr(entry, "link", "") or "").strip()

                    if not title or not link:
                        continue

                    pub_parsed = (
                        getattr(entry, "published_parsed", None)
                        or getattr(entry, "updated_parsed", None)
                    )

                    if pub_parsed:
                        pub_time = datetime(*pub_parsed[:6]).replace(tzinfo=pytz.utc)
                        age_mins = (now_utc - pub_time).total_seconds() / 60

                        if 0 <= age_mins <= time_gap_minutes and link not in seen_log:
                            fresh_extracted_stories.append({
                                "category": category,
                                "title": title,
                                "link": link,
                            })
            except Exception as e:
                print(f"[WARN] Failed to parse {category} ({working_url}): {e}")
                continue
        else:
            health_data[category] = {
                "status": "DEAD",
                "url_used": None,
                "auto_fixed": False,
                "fix_method": None,
            }
            # A feed that's fully dead (repair chain exhausted, no reachable
            # URL at all) can't have a valid remembered fix either -- drop
            # any stale override so we don't keep offering a URL that no
            # longer resolves anything.
            if feed_overrides.pop(category, None) is not None:
                overrides_changed = True
                print(f"[SELF-HEALING AGENT] Cleared stale override for {category} (feed is fully dead).")

    with open(FEED_HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health_data, f, indent=2)

    if overrides_changed:
        with open(FEED_OVERRIDES_FILE, "w", encoding="utf-8") as f:
            json.dump(feed_overrides, f, indent=2)
        print(f"[SELF-HEALING AGENT] Saved {len(feed_overrides)} feed override(s) to {FEED_OVERRIDES_FILE}.")

    random.shuffle(fresh_extracted_stories)
    deduped_candidates = []
    for story in fresh_extracted_stories:
        is_duplicate = False
        for chosen in deduped_candidates:
            if get_title_similarity(story["title"], chosen["title"]) >= TITLE_SIMILARITY_THRESHOLD:
                is_duplicate = True
                break
        if not is_duplicate:
            deduped_candidates.append(story)
            if len(deduped_candidates) >= MAX_SUGGESTIONS_PER_RUN:
                break

    def fetch_for_story(story):
        section = fetch_article_section(story["link"])
        return story, section

    clean_suggestions = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetch_for_story, story) for story in deduped_candidates]
        for future in as_completed(futures):
            story, section = future.result()
            if not section:
                continue
            story["summary"] = section
            story["sensitive"] = (
                contains_sensitive_content(story["title"])
                or contains_sensitive_content(section)
            )
            clean_suggestions.append(story)
            if story.get("link"):
                seen_log.append(story["link"])

    candidate_order = {c["link"]: i for i, c in enumerate(deduped_candidates)}
    clean_suggestions.sort(key=lambda s: candidate_order.get(s["link"], 0))

    # ------------------------------------------------------------------
    # CHANGED: suggested_posts.md is now APPENDED to, never overwritten.
    # Every run's new stories are added under a fresh dated section at
    # the bottom of the file. Nothing already written is ever deleted
    # by the scanner itself -- Kefa is the only one who removes lines,
    # by deleting them manually after posting them to Facebook.
    # ------------------------------------------------------------------
    if clean_suggestions:
        file_is_new = not os.path.exists(SUGGESTED_POSTS_FILE) or os.path.getsize(SUGGESTED_POSTS_FILE) == 0
        with open(SUGGESTED_POSTS_FILE, "a", encoding="utf-8") as f:
            if file_is_new:
                f.write("# Kenya News Suggestions\n\n")
                f.write("Delete a story's lines after you've posted it, so this file only ever shows what's still pending.\n\n")
            f.write(f"## Run: {now_utc.astimezone(NAIROBI_TZ).strftime('%Y-%m-%d %H:%M:%S')} EAT\n\n")
            f.write(f"Scanned lookback gap of {time_gap_minutes} minutes. Found {len(clean_suggestions)} unique stories.\n\n")
            if suggested_fixes:
                new_fixes = [fx for fx in suggested_fixes if not fx["reused"]]
                reused_fixes = [fx for fx in suggested_fixes if fx["reused"]]
                if new_fixes:
                    f.write("### Feed URLs auto-fixed this run (update FEEDS in news_scanner.py)\n\n")
                    for fix in new_fixes:
                        f.write(
                            f"- **{fix['category']}** ({fix['method']}): "
                            f"`{fix['original_url']}` -> `{fix['working_url']}`\n"
                        )
                    f.write("\n")
                if reused_fixes:
                    f.write("### Feed URLs still running on a remembered fix\n\n")
                    for fix in reused_fixes:
                        f.write(f"- **{fix['category']}**: `{fix['working_url']}`\n")
                    f.write("\n")
            f.write("---\n\n")

            for index, item in enumerate(clean_suggestions, 1):
                sensitive_tag = "⚠️ [SENSITIVE] " if item["sensitive"] else ""
                f.write(f"**{index}. {sensitive_tag}{item['title']}**\n\n")
                f.write(f"{item['summary']}\n\n")
                f.write("---\n\n")
        print(f"[SUCCESS] Appended {len(clean_suggestions)} unique updates to {SUGGESTED_POSTS_FILE}.")
    else:
        print(f"[INFO] Zero new entries published in the tracked {time_gap_minutes}-minute timeframe gap.")

    if len(seen_log) > 1200:
        print("[SELF-HEALING AGENT] Pruning log entries to protect memory space...")
        seen_log = seen_log[-1200:]

    with open(SEEN_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_log, f)

    with open(LAST_RUN_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_successful_run_utc": now_utc.isoformat()}, f)
    print(f"[STATE SAVED] Last successful run timestamp locked at {now_utc.isoformat()}.")


if __name__ == "__main__":
    main()
