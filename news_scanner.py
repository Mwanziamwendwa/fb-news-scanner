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

# Local Kenyan timezone synchronization
NAIROBI_TZ = pytz.timezone("Africa/Nairobi")

SUGGESTED_POSTS_FILE = "suggested_posts.md"
SEEN_LOG_FILE = "seen_log.json"              
FEED_HEALTH_FILE = "feed_health.json"        

# File used by the Dynamic Agent to track exactly when your last manual scan completed
LAST_RUN_STATE_FILE = "last_run_state.json"

MAX_SUGGESTIONS_PER_RUN = 100          
LOG_RETENTION_DAYS = 14               
FEED_TIMEOUT_SECONDS = 15
ARTICLE_FETCH_TIMEOUT_SECONDS = 15
FEED_HEALTH_CHECK_TIMEOUT_SECONDS = 10

TITLE_SIMILARITY_THRESHOLD = 0.45

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
    r"become a member to"
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
    if not text: return ""
    cleaned_text = re.sub(r'<[^<]+?>', '', text)
    cleaned_text = html.unescape(cleaned_text)
    # Normalize BEFORE pattern matching, not after: real-world CMS content
    # (Standard Media in particular) frequently uses &nbsp; for spacing --
    # html.unescape() turns that into U+00A0, not a plain space -- and
    # curly quotes (&#8217;) instead of straight apostrophes. The literal
    # spaces and straight "'" baked into PAYWALL_AND_BIO_PATTERNS silently
    # fail to match across either of those, letting whole boilerplate
    # sentences slip through untouched.
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text)
    cleaned_text = cleaned_text.replace('\u2019', "'").replace('\u2018', "'")
    for pattern in PAYWALL_AND_BIO_PATTERNS:
        cleaned_text = re.sub(pattern, "", cleaned_text, flags=re.IGNORECASE | re.DOTALL)
    cleaned_text = re.sub(r'\s+', ' ', cleaned_text).strip()
    # Removing a boilerplate sentence from the middle/start/end can leave
    # an orphaned punctuation mark where the boilerplate used to connect
    # to real content (e.g. ". Treasury Secretary..." or "...enterprises. .")
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
    if not tokens1 or not tokens2: return 0.0
    common = tokens1.intersection(tokens2)
    return len(common) / min(len(tokens1), len(tokens2))

def is_link_reachable(url):
    try:
        res = requests.head(url, headers=FEED_REQUEST_HEADERS, timeout=FEED_HEALTH_CHECK_TIMEOUT_SECONDS)
        if res.status_code < 400:
            return True
        # Some servers reject HEAD (403/405) but are fine with GET.
        # stream=True defers the body download; close the connection
        # explicitly since we only need the status code, not the content.
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
    """
    Cheap, mechanical rewrites to try when a feed URL 404s/times out —
    covers the most common ways these URLs rot over time (protocol
    change, added/dropped www, trailing slash, /feed vs /rss/, or a
    dropped /feed suffix entirely). Not a guess at new content, just
    URL-shape permutations of the exact same address.
    """
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
    """
    Falls back to fetching the site's homepage and reading its
    <link rel="alternate" type="application/rss+xml"> tag — the
    standard way browsers/readers find a site's real feed URL. Returns
    None (never raises) if the homepage can't be reached or has no
    such tag.
    """
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
    """
    Last-resort tier: builds a real, valid Google News RSS search feed
    for the feed's category name (e.g. "Politics" -> Kenya politics
    news). This is a genuine working RSS endpoint -- unlike a raw
    Google homepage URL -- so if a publisher's own feed is gone for
    good, coverage of that category can still continue via Google
    News search results until the real feed is fixed or replaced.
    """
    query = requests.utils.quote(f"Kenya {category}")
    return f"https://news.google.com/rss/search?q={query}&hl=en-KE&gl=KE&ceid=KE:en"


def _is_valid_feed(url):
    """
    HTTP-reachable isn't the same as "is actually an RSS/Atom feed" --
    a rewritten URL (e.g. dropped /feed suffix) can 200 with the site's
    homepage HTML instead. Confirm feedparser can find at least one
    entry before treating a candidate as a real fix, so feed_health.json
    doesn't report ALIVE for a fix that silently yields nothing.
    """
    try:
        parsed = feedparser.parse(url)
        return bool(parsed.entries)
    except Exception:
        return False


def find_working_feed_url(original_url, category=None):
    """
    Self-healing agent: when a configured feed URL is unreachable,
    tries mechanical URL variants first (fast, no guessing at content),
    then falls back to reading the real feed URL off the site's
    homepage, then finally to a genuine Google News search feed for
    that category so coverage continues even if the original site is
    down for good. Returns (working_url, method) if something
    reachable was found, or (None, None) if nothing worked -- in which
    case the feed is skipped this run exactly as before.
    """
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
        if term in text_lower: return True
    return False


# ==============================================================================
# MAIN SCANNER ENGINE
# ==============================================================================
def main():
    print("[PIPELINE RUN] Starting Dynamic Multi-Agent News Scanner...")
    now_utc = datetime.now(pytz.utc)
    
    # ----------------==========================================================
    # DYNAMIC AGENT: TIME GAP CALCULATION (WITH 15-MIN SHORT RUN PROTECTION)
    # ----------------==========================================================
    last_run_time = now_utc - timedelta(hours=24) 
    if os.path.exists(LAST_RUN_STATE_FILE):
        try:
            with open(LAST_RUN_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                last_run_time = datetime.fromisoformat(state["last_successful_run_utc"])
                print(f"[DYNAMIC AGENT] Last scan detected at: {last_run_time.astimezone(NAIROBI_TZ)}")
        except:
            print("[DYNAMIC AGENT] State file unreadable. Defaulting parameters.")

    time_gap_seconds = (now_utc - last_run_time).total_seconds()
    time_gap_minutes = int(time_gap_seconds / 60)

    # 15-Minute Safety Window Catch for back-to-back runs
    if time_gap_minutes < 15:
        print(f"[DYNAMIC AGENT] Quick run detected ({time_gap_minutes} mins gap). Enforcing 15-minute safety lookup window.")
        time_gap_minutes = 15
    else:
        print(f"[DYNAMIC AGENT] Lookback window configured to capture the last: {time_gap_minutes} minutes.")

    seen_log = []
    if os.path.exists(SEEN_LOG_FILE):
        try:
            with open(SEEN_LOG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                seen_log = loaded
            elif isinstance(loaded, dict):
                # Old format from an earlier script version stored
                # {title: timestamp} instead of a list of links.
                # Migrate by keeping the keys so old entries still
                # count as "seen" instead of silently crashing.
                print("[WARN] seen_log.json is in an old dict format, migrating to list.")
                seen_log = list(loaded.keys())
            else:
                print("[WARN] seen_log.json has an unexpected structure, starting fresh.")
        except (OSError, json.JSONDecodeError, ValueError):
            print("[WARN] Could not read seen_log.json, starting fresh.")

    health_data = {}
    fresh_extracted_stories = []
    suggested_fixes = []

    def resolve_feed(feed):
        """
        Reachability check + self-healing repair for one feed. Run in a
        thread pool below -- with ~60 feeds each carrying up to a
        10-15s timeout, doing this sequentially could stretch a single
        run to many minutes whenever several feeds are down at once.
        """
        working_url = feed["url"]
        fix_method = None

        if not is_link_reachable(working_url):
            # ---------------------------------------------------------
            # SELF-HEALING AGENT: FEED URL REPAIR
            # ---------------------------------------------------------
            # A dead feed used to just get skipped for the run and
            # left broken until someone noticed. Instead, try to find
            # a working replacement URL automatically before giving up.
            print(f"[SELF-HEALING AGENT] {feed['category']} unreachable, attempting repair...")
            fixed_url, fix_method = find_working_feed_url(feed["url"], category=feed["category"])
            if fixed_url:
                print(f"[SELF-HEALING AGENT] Fixed {feed['category']} via {fix_method}: {fixed_url}")
                working_url = fixed_url
            else:
                working_url = None

        return feed, working_url, fix_method

    # Resolve every feed's working URL concurrently rather than one at
    # a time; this is the expensive part of the run (network round
    # trips), not the actual feed parsing below.
    resolved = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(resolve_feed, feed) for feed in FEEDS]
        for future in as_completed(futures):
            resolved.append(future.result())

    # Preserve FEEDS order for deterministic health_data/log output.
    resolved.sort(key=lambda r: FEEDS.index(r[0]))

    # Extraction Pass
    for feed, working_url, fix_method in resolved:
        if working_url:
            health_data[feed["category"]] = {
                "status": "ALIVE",
                "url_used": working_url,
                "auto_fixed": fix_method is not None,
                "fix_method": fix_method,
            }
            if fix_method:
                suggested_fixes.append({
                    "category": feed["category"],
                    "original_url": feed["url"],
                    "working_url": working_url,
                    "method": fix_method,
                })
            try:
                parsed = feedparser.parse(working_url)
                for entry in parsed.entries:
                    title = (getattr(entry, "title", "") or "").strip()
                    link = (getattr(entry, "link", "") or "").strip()

                    # Skip malformed RSS entries without a title or link.
                    if not title or not link:
                        continue

                    pub_parsed = (
                        getattr(entry, "published_parsed", None)
                        or getattr(entry, "updated_parsed", None)
                    )
                    
                    if pub_parsed:
                        pub_time = datetime(*pub_parsed[:6]).replace(tzinfo=pytz.utc)
                        age_mins = (now_utc - pub_time).total_seconds() / 60
                        
                        # Dynamic lookback bounds check
                        if 0 <= age_mins <= time_gap_minutes and link not in seen_log:
                            raw_summary = getattr(entry, "summary", "") or getattr(entry, "description", "")
                            processed_snippet = clean_and_strip_paywall_text(raw_summary)
                            
                            if len(processed_snippet) < 15: continue

                            fresh_extracted_stories.append({
                                "category": feed["category"],
                                "title": title,
                                "link": link,
                                "summary": processed_snippet,
                            })
            except Exception as e:
                print(f"[WARN] Failed to parse {feed['category']} ({working_url}): {e}")
                continue
        else:
            health_data[feed["category"]] = {
                "status": "DEAD",
                "url_used": None,
                "auto_fixed": False,
                "fix_method": None,
            }

    with open(FEED_HEALTH_FILE, "w", encoding="utf-8") as f:
        json.dump(health_data, f, indent=2)

    # Cross-Feed Deduplication & Sensitivity Inspection
    #
    # Shuffled before the per-run cap is applied: FEEDS is a fixed list,
    # so scanning in that order every run means feeds near the top
    # (Google News, Standard Media, ...) can fill MAX_SUGGESTIONS_PER_RUN
    # on their own and feeds near the bottom (Kenyapedia, ...) never get
    # a chance to contribute. Shuffling per run gives every feed a fair
    # shot over time instead of the same feeds winning every single run.
    random.shuffle(fresh_extracted_stories)
    clean_suggestions = []
    for story in fresh_extracted_stories:
        is_duplicate = False
        for chosen in clean_suggestions:
            if get_title_similarity(story["title"], chosen["title"]) >= TITLE_SIMILARITY_THRESHOLD:
                is_duplicate = True
                break
        
        if not is_duplicate:
            story["sensitive"] = (
                contains_sensitive_content(story["title"])
                or contains_sensitive_content(story["summary"])
            )
            clean_suggestions.append(story)

            # Only store valid links in the seen log.
            if story.get("link"):
                seen_log.append(story["link"])

            # Enforce the configured maximum suggestions per run.
            if len(clean_suggestions) >= MAX_SUGGESTIONS_PER_RUN:
                break

    # Output to suggested_posts.md
    if clean_suggestions:
        with open(SUGGESTED_POSTS_FILE, "w", encoding="utf-8") as f:
            f.write(f"# Kenya News Suggestions - Generated {now_utc.astimezone(NAIROBI_TZ).strftime('%Y-%m-%d %H:%M:%S')} EAT\n\n")
            f.write(f"Scanned lookback gap of {time_gap_minutes} minutes. Found {len(clean_suggestions)} unique stories.\n\n")
            if suggested_fixes:
                f.write("## Feed URLs auto-fixed this run (update FEEDS in news_scanner.py)\n\n")
                for fix in suggested_fixes:
                    f.write(
                        f"- **{fix['category']}** ({fix['method']}): "
                        f"`{fix['original_url']}` → `{fix['working_url']}`\n"
                    )
                f.write("\n")
            f.write("---\n\n")
            
            for index, item in enumerate(clean_suggestions, 1):
                sensitive_tag = "⚠️ [SENSITIVE] " if item["sensitive"] else ""
                # Post-ready format: bold headline, then the cleaned summary
                # as one flowing paragraph directly beneath it (no bullets),
                # so this can be copied straight into a Facebook post. Link
                # and category are kept as a light reference line underneath
                # rather than dropped entirely -- still needed to trace the
                # story back to its source -- just not styled as metadata.
                f.write(f"**{index}. {sensitive_tag}{item['title']}**\n\n")
                f.write(f"{item['summary']}\n\n")
                f.write(f"_{item['category']} — {item['link']}_\n")
                f.write("\n---\n\n")
        print(f"[SUCCESS] Saved {len(clean_suggestions)} unique updates to {SUGGESTED_POSTS_FILE}.")
    else:
        print(f"[INFO] Zero new entries published in the tracked {time_gap_minutes}-minute timeframe gap.")

    # ----------------==========================================================
    # SELF-HEALING AGENT: LOG PRUNING
    # ----------------==========================================================
    if len(seen_log) > 1200:
        print("[SELF-HEALING AGENT] Pruning log entries to protect memory space...")
        seen_log = seen_log[-1200:]
        
    with open(SEEN_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_log, f)

    # Lock state timestamp
    with open(LAST_RUN_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"last_successful_run_utc": now_utc.isoformat()}, f)
    print(f"[STATE SAVED] Last successful run timestamp locked at {now_utc.isoformat()}.")


if __name__ == "__main__":
    main()
