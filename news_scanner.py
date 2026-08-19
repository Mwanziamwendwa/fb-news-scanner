"""
Kenya News Scanner — Broad Coverage / Growth Mode

Pulls Kenyan news from Google News RSS + direct outlet feeds,
dedupes against previously suggested stories, holds back
graphic/sensitive content for manual review, and writes
candidates to suggested_posts.md.

This script does NOT post to Facebook.
It only generates suggestions.

You copy the suggested headlines into Claude for rewriting,
then post manually.

No environment variables or secrets required.
This script only reads RSS feeds and writes local files.
"""

import os
import json
import feedparser
import socket

from datetime import datetime, timedelta

import pytz


# ============================================================
# CONFIG
# ============================================================

NAIROBI_TZ = pytz.timezone("Africa/Nairobi")

SUGGESTED_POSTS_FILE = "suggested_posts.md"
POSTED_LOG_FILE = "posted_log.json"


# ============================================================
# RSS FEEDS
# ============================================================

FEEDS = [

    # ========================================================
    # GOOGLE NEWS RSS
    # ========================================================

    {
        "category": "Kenya Latest",
        "url": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSkwyMHZNREU1Y21jMUVnVmxiaTFIUWlnQVAB?hl=en-KE&gl=KE&ceid=KE%3Aen"
    },

    {
        "category": "General",
        "url": "https://news.google.com/rss/topics/CAAqJggKIiBDQkFTRWdvSkwyMHZNREU1Y21jMUVnVmxiaTFIUWlnQVAB?hl=en-KE&gl=KE&ceid=KE:en"
    },

    {
        "category": "Politics",
        "url": "https://news.google.com/rss/search?q=Kenya+politics&hl=en-KE&gl=KE&ceid=KE:en"
    },

    {
        "category": "Business",
        "url": "https://news.google.com/rss/search?q=Kenya+business+economy&hl=en-KE&gl=KE&ceid=KE:en"
    },

    {
        "category": "Business Topic",
        "url": "https://news.google.com/rss/topics/CAAqKggKIiRDQkFTRlFvSUwyMHZNRGx6TVdZU0JXVnVMVWRDR2dKTFJTZ0FQAQ?hl=en-KE&gl=KE&ceid=KE:en"
    },

    {
        "category": "Entertainment",
        "url": "https://news.google.com/rss/search?q=Kenya+entertainment+celebrity&hl=en-KE&gl=KE&ceid=KE:en"
    },

    {
        "category": "Sports",
        "url": "https://news.google.com/rss/search?q=Kenya+sports+football&hl=en-KE&gl=KE&ceid=KE:en"
    },

    {
        "category": "Trending",
        "url": "https://news.google.com/rss/search?q=Kenya+viral+trending&hl=en-KE&gl=KE&ceid=KE:en"
    },


    # ========================================================
    # KENYAN NEWS SITES
    # ========================================================

    {
        "category": "Nairobi Leo",
        "url": "https://nairobileo.co.ke/feed"
    },

    {
        "category": "SPM Buzz",
        "url": "https://spmbuzz.com/feed/"
    },

    {
        "category": "Ghafla",
        "url": "https://www.ghafla.co.ke/feed/"
    },

    {
        "category": "Ghafla KE",
        "url": "https://ghafla.co.ke/ke/feed"
    },

    {
        "category": "K24 Digital",
        "url": "https://k24.digital/feed"
    },

    {
        "category": "KBC Digital",
        "url": "https://kbc.co.ke/feed"
    },

    {
        "category": "NTV Kenya",
        "url": "https://ntvkenya.co.ke/feed/"
    },

    {
        "category": "Kenyans.co.ke",
        "url": "https://www.kenyans.co.ke/feeds/news"
    },

    {
        "category": "Nation Africa",
        "url": "https://nation.africa/kenya/rss.xml"
    },

    {
        "category": "Business Daily Africa",
        "url": "https://www.businessdailyafrica.com/service/rss/bd/1939132/feed.rss"
    },

    {
        "category": "Kenyan Wall Street",
        "url": "https://kenyanwallstreet.com/feed/"
    },

    {
        "category": "Kenya News Agency",
        "url": "https://www.kenyanews.go.ke/feed/"
    },

    {
        "category": "Taifa Leo",
        "url": "https://taifaleo.nation.co.ke/feed"
    },

    {
        "category": "Nairobi Wire",
        "url": "https://nairobiwire.com/feed"
    },

    {
        "category": "Diaspora Messenger",
        "url": "https://diasporamessenger.com/feed/"
    },

    {
        "category": "Mwakilishi",
        "url": "https://mwakilishi.com/feed"
    },

    {
        "category": "Sharp Daily",
        "url": "https://sharpdaily.co.ke/feed/"
    },

    {
        "category": "News Trends KE",
        "url": "https://newstrends.co.ke/feed/"
    },

    {
        "category": "Sauce Kenya",
        "url": "https://sauce.co.ke/feed/"
    },

    {
        "category": "The Kenya Times",
        "url": "https://thekenyatimes.com/feed/"
    },

    {
        "category": "Aipate News",
        "url": "https://aipate.com/category/news/feed"
    },

    {
        "category": "KenyaMOJA",
        "url": "https://www.kenyamoja.com/news/nairobi-leo/feed"
    },

    {
        "category": "Nairobi Gossip Club",
        "url": "https://nairobigossipclub.co.ke/feeds"
    },

    # Education News — added
    {
        "category": "Education News",
        "url": "https://educationnews.co.ke/feed/"
    },


    # ========================================================
    # STANDARD MEDIA
    # ========================================================

    {
        "category": "Standard Headlines",
        "url": "https://www.standardmedia.co.ke/rss/headlines.php"
    },

    {
        "category": "Standard Kenya",
        "url": "https://www.standardmedia.co.ke/rss/kenya.php"
    },

    {
        "category": "Standard Politics",
        "url": "https://www.standardmedia.co.ke/rss/politics.php"
    },

    {
        "category": "Standard Sports",
        "url": "https://www.standardmedia.co.ke/rss/sports.php"
    },

    {
        "category": "Standard World",
        "url": "https://www.standardmedia.co.ke/rss/world.php"
    },


    # ========================================================
    # CAPITAL FM
    # ========================================================

    {
        "category": "Capital FM News",
        "url": "https://capitalfm.africa/news/feed/"
    },

    {
        "category": "Capital FM Sports",
        "url": "https://capitalfm.africa/sports/feed/"
    },

    {
        "category": "Capital FM Lifestyle",
        "url": "https://capitalfm.africa/lifestyle/feed/"
    },

    {
        "category": "Capital FM Business",
        "url": "https://capitalfm.africa/business/feed/"
    },

    {
        "category": "Capital FM Kenya",
        "url": "https://www.capitalfm.co.ke/news/feed/"
    },


    # ========================================================
    # GETEMBE TV
    # ========================================================

    {
        "category": "Getembe Latest",
        "url": "https://getembetv.co.ke/rss/latest-posts"
    },

    {
        "category": "Getembe News",
        "url": "https://getembetv.co.ke/rss/category/news"
    },

    {
        "category": "Getembe Business",
        "url": "https://getembetv.co.ke/rss/category/business"
    },

    {
        "category": "Getembe Education",
        "url": "https://getembetv.co.ke/rss/category/education"
    },

    {
        "category": "Getembe Politics",
        "url": "https://getembetv.co.ke/rss/category/politics"
    },

    {
        "category": "Getembe Health",
        "url": "https://getembetv.co.ke/rss/category/health"
    },


    # ========================================================
    # VIRAL TEA
    # ========================================================

    {
        "category": "Viral Tea Latest",
        "url": "https://viraltea.co.ke/rss/latest-posts"
    },

    {
        "category": "Viral Tea News",
        "url": "https://viraltea.co.ke/rss/category/news"
    },

    {
        "category": "Viral Tea Breaking",
        "url": "https://viraltea.co.ke/rss/category/breaking"
    },

    {
        "category": "Viral Tea National",
        "url": "https://viraltea.co.ke/rss/category/national"
    },

    {
        "category": "Viral Tea Local",
        "url": "https://viraltea.co.ke/rss/category/local"
    },


    # ========================================================
    # KENYAPEDIA
    # ========================================================

    {
        "category": "Kenyapedia Latest",
        "url": "https://www.kenyapedia.co.ke/rss/latest-posts"
    },

    {
        "category": "Kenyapedia Jobs",
        "url": "https://www.kenyapedia.co.ke/rss/category/jobs"
    },

    {
        "category": "Kenyapedia Money",
        "url": "https://www.kenyapedia.co.ke/rss/category/money-and-finances"
    },

    {
        "category": "Kenyapedia Grants",
        "url": "https://www.kenyapedia.co.ke/rss/category/business-grants-and-financing"
    },

    {
        "category": "Kenyapedia Debt",
        "url": "https://www.kenyapedia.co.ke/rss/category/debt-and-borrowing"
    },

    {
        "category": "Kenyapedia Economy",
        "url": "https://www.kenyapedia.co.ke/rss/category/kenya-economy"
    },

    {
        "category": "Kenyapedia Education",
        "url": "https://www.kenyapedia.co.ke/rss/category/education-funding"
    },

    {
        "category": "Kenyapedia Taxes",
        "url": "https://www.kenyapedia.co.ke/rss/category/taxes-50"
    },

    {
        "category": "Kenyapedia Investments",
        "url": "https://www.kenyapedia.co.ke/rss/category/savings-and-investments"
    },

    {
        "category": "Kenyapedia Recent News",
        "url": "https://www.kenyapedia.co.ke/rss/category/recent-news"
    },
]


# ============================================================
# REQUEST SETTINGS
# ============================================================

FEED_REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; KenyaNewsScanner/1.0; "
        "+https://github.com/)"
    )
}


# ============================================================
# SENSITIVE CONTENT FILTER
# ============================================================

SENSITIVE_TERMS = [
    "autopsy",
    "mutilated",
    "beheaded",
    "dismembered",
    "gore",
    "graphic images",
    "explicit video",
    "child abuse",
    "defiled",
    "gang rape",
    "mob justice",
    "lynched",
]


# ============================================================
# SCANNER SETTINGS
# ============================================================

# Maximum number of new suggestions produced in one run.
MAX_SUGGESTIONS_PER_RUN = 25

# Look back this many hours when checking RSS publication dates.
LOOKBACK_HOURS = 48

# Maximum time allowed for an individual feed.
FEED_TIMEOUT_SECONDS = 15


# ============================================================
# HELPERS
# ============================================================

def normalize_title(title):
    """
    Lowercase and collapse whitespace.

    Used as the deduplication key.
    """
    return " ".join(title.lower().split())


def load_posted_log():
    """
    Load previously seen/suggested titles.
    """
    if os.path.exists(POSTED_LOG_FILE):

        try:

            with open(
                POSTED_LOG_FILE,
                "r",
                encoding="utf-8"
            ) as f:

                data = json.load(f)

                if isinstance(data, list):
                    return set(data)

                if isinstance(data, dict):
                    return set(data.keys())

                return set()

        except (
            json.JSONDecodeError,
            ValueError,
            OSError
        ):

            print(
                f"[WARN] {POSTED_LOG_FILE} was invalid JSON "
                "— starting fresh."
            )

            return set()

    return set()


def save_posted_log(seen_titles):
    """
    Save the deduplication log.
    """
    try:

        with open(
            POSTED_LOG_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                sorted(seen_titles),
                f,
                indent=2,
                ensure_ascii=False
            )

    except OSError as e:

        print(
            f"[ERROR] Could not save {POSTED_LOG_FILE}: {e}"
        )


def is_recent(published_parsed):
    """
    Return True if the story is within LOOKBACK_HOURS.

    If no publication date is available, keep the story.
    """

    if not published_parsed:
        return True

    try:

        published_dt = datetime(
            *published_parsed[:6],
            tzinfo=pytz.utc
        )

    except (
        TypeError,
        ValueError,
        OverflowError
    ):

        return True

    cutoff = (
        datetime.now(pytz.utc)
        - timedelta(hours=LOOKBACK_HOURS)
    )

    return published_dt >= cutoff


def is_sensitive(title, summary):
    """
    Check title + summary against the sensitive-content list.
    """

    text = f"{title} {summary}".lower()

    return any(
        term in text
        for term in SENSITIVE_TERMS
    )


def nairobi_timestamp():
    """
    Return current Nairobi time.
    """

    return datetime.now(
        NAIROBI_TZ
    ).strftime(
        "%Y-%m-%d %H:%M"
    )


def parse_feed_safely(feed_url):
    """
    Parse an RSS feed with:

    - custom User-Agent
    - socket timeout
    - safe restoration of previous timeout
    """

    old_timeout = socket.getdefaulttimeout()

    socket.setdefaulttimeout(
        FEED_TIMEOUT_SECONDS
    )

    try:

        return feedparser.parse(
            feed_url,
            request_headers=FEED_REQUEST_HEADERS
        )

    finally:

        socket.setdefaulttimeout(
            old_timeout
        )


def clean_text(value):
    """
    Remove excessive whitespace from RSS text.
    """

    if not value:
        return ""

    return " ".join(
        str(value).split()
    )


def get_source_name(title, category):
    """
    Google News titles often look like:

        Headline - Nation

    Extract the source name when possible.
    """

    if " - " in title:

        source_name = (
            title.rsplit(
                " - ",
                1
            )[-1]
        ).strip()

        if source_name:
            return source_name

    return category


def append_suggestions(entries):
    """
    Append new stories to suggested_posts.md.
    """

    file_exists = os.path.exists(
        SUGGESTED_POSTS_FILE
    )

    try:

        with open(
            SUGGESTED_POSTS_FILE,
            "a",
            encoding="utf-8"
        ) as f:

            if not file_exists:

                f.write(
                    "# Suggested Posts\n\n"
                )

            f.write(
                f"## Scan run: "
                f"{nairobi_timestamp()} "
                f"(Nairobi time)\n\n"
            )

            for entry in entries:

                if entry["flagged"]:

                    flag = (
                        " ⚠️ REVIEW — possibly "
                        "sensitive content"
                    )

                else:

                    flag = ""

                f.write(
                    f"- **[{entry['category']}]** "
                    f"{entry['title']}"
                    f"{flag}\n"
                )

                f.write(
                    f"  - Source: "
                    f"{entry['source']}\n"
                )

                f.write(
                    f"  - Link: "
                    f"{entry['link']}\n\n"
                )

    except OSError as e:

        print(
            f"[ERROR] Could not write "
            f"{SUGGESTED_POSTS_FILE}: {e}"
        )


# ============================================================
# MAIN SCANNER
# ============================================================

def main():

    print("=" * 70)
    print("KENYA NEWS SCANNER")
    print("=" * 70)

    print(
        f"Scan time: {nairobi_timestamp()} "
        f"(Nairobi)"
    )

    print(
        f"RSS feeds configured: {len(FEEDS)}"
    )

    print(
        f"Lookback: {LOOKBACK_HOURS} hours"
    )

    print(
        f"Maximum suggestions: "
        f"{MAX_SUGGESTIONS_PER_RUN}"
    )

    print("=" * 70)

    seen_titles = load_posted_log()

    new_entries = []

    suggestions_made = 0

    feeds_checked = 0

    feeds_failed = 0


    # --------------------------------------------------------
    # PROCESS EACH FEED
    # --------------------------------------------------------

    for feed_source in FEEDS:

        if (
            suggestions_made
            >= MAX_SUGGESTIONS_PER_RUN
        ):
            break

        category = feed_source.get(
            "category",
            "Unknown"
        )

        feed_url = feed_source.get(
            "url",
            ""
        )

        if not feed_url:

            print(
                f"[WARN] Empty URL for "
                f"{category}"
            )

            feeds_failed += 1

            continue

        print(
            f"[CHECK] {category}"
        )

        try:

            feed = parse_feed_safely(
                feed_url
            )

            feeds_checked += 1

        except Exception as e:

            feeds_failed += 1

            print(
                f"[WARN] Could not parse "
                f"{category}: {e}"
            )

            continue


        # ----------------------------------------------------
        # CHECK FOR FEED ERRORS
        # ----------------------------------------------------

        if (
            getattr(feed, "bozo", False)
            and not feed.entries
        ):

            feeds_failed += 1

            print(
                f"[WARN] {category} returned "
                f"no usable entries."
            )

            print(
                f"       URL: {feed_url}"
            )

            continue


        if not feed.entries:

            print(
                f"[INFO] {category}: "
                f"no entries."
            )

            continue


        print(
            f"[OK] {category}: "
            f"{len(feed.entries)} entries"
        )


        # ----------------------------------------------------
        # PROCESS STORIES
        # ----------------------------------------------------

        for entry in feed.entries:

            if (
                suggestions_made
                >= MAX_SUGGESTIONS_PER_RUN
            ):
                break


            title = clean_text(
                entry.get(
                    "title",
                    ""
                )
            )

            link = clean_text(
                entry.get(
                    "link",
                    ""
                )
            )

            summary = clean_text(
                entry.get(
                    "summary",
                    ""
                )
            )

            published_parsed = (
                entry.get(
                    "published_parsed"
                )
                or entry.get(
                    "updated_parsed"
                )
            )


            # ------------------------------------------------
            # REQUIRED FIELDS
            # ------------------------------------------------

            if not title:

                continue

            if not link:

                continue


            # ------------------------------------------------
            # DUPLICATE CHECK
            # ------------------------------------------------

            key = normalize_title(
                title
            )

            if key in seen_titles:

                continue


            # ------------------------------------------------
            # DATE CHECK
            # ------------------------------------------------

            if not is_recent(
                published_parsed
            ):

                continue


            # ------------------------------------------------
            # SENSITIVE CONTENT CHECK
            # ------------------------------------------------

            flagged = is_sensitive(
                title,
                summary
            )


            # ------------------------------------------------
            # SOURCE
            # ------------------------------------------------

            source_name = get_source_name(
                title,
                category
            )


            # ------------------------------------------------
            # ADD STORY
            # ------------------------------------------------

            new_entries.append(
                {
                    "category": category,
                    "title": title,
                    "link": link,
                    "source": source_name,
                    "flagged": flagged,
                }
            )


            # Add immediately so duplicate stories
            # appearing in another feed during this
            # same run are not added again.
            seen_titles.add(key)

            suggestions_made += 1


    # ========================================================
    # WRITE RESULTS
    # ========================================================

    if new_entries:

        append_suggestions(
            new_entries
        )

        save_posted_log(
            seen_titles
        )

        flagged_count = sum(
            1
            for entry in new_entries
            if entry["flagged"]
        )

        clean_count = (
            len(new_entries)
            - flagged_count
        )


        print()
        print("=" * 70)
        print("SCAN COMPLETE")
        print("=" * 70)

        print(
            f"New stories: {len(new_entries)}"
        )

        print(
            f"Normal suggestions: {clean_count}"
        )

        print(
            f"Flagged for review: {flagged_count}"
        )

        print(
            f"Feeds checked: {feeds_checked}"
        )

        print(
            f"Feed failures: {feeds_failed}"
        )

        print(
            f"Output: {SUGGESTED_POSTS_FILE}"
        )

        print(
            f"Log: {POSTED_LOG_FILE}"
        )

        print("=" * 70)

    else:

        # Save the log even if no new stories
        # were found, so the file remains valid.
        save_posted_log(
            seen_titles
        )

        print()
        print("=" * 70)
        print("SCAN COMPLETE")
        print("=" * 70)

        print(
            "No new stories found this run."
        )

        print(
            f"Feeds checked: {feeds_checked}"
        )

        print(
            f"Feed failures: {feeds_failed}"
        )

        print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
