#!/usr/bin/env python3
"""
fb_news_scanner.py

Scans Kenyan teacher/TSC news feeds, picks the top MAJOR story that hasn't
been posted yet, has the next agent in the rotation rewrite it as a native
Facebook post, and publishes it via the Graph API.

Runs ONLY when triggered (manual workflow_dispatch in GitHub Actions).
Each run posts at most ONE story and hands the turn to the next agent,
so agents genuinely take turns rather than all firing on autopilot.

STATE (whose turn it is, which stories are already posted) is stored
directly inside THIS FILE, between the STATE START / STATE END markers
below. No separate JSON file is used. After each run, the script rewrites
its own STATE block, and the GitHub Actions workflow commits the updated
.py file back to the repo.
"""

import os
import re
import hashlib
import feedparser
import requests
from datetime import timedelta, timezone

# ============================================================
# STATE START -- do not hand-edit the layout of this block,
# only the values, if you ever need to reset something manually.
NEXT_AGENT_INDEX = 0
POSTED_IDS = []
# STATE END
# ============================================================

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

NAIROBI_TZ = timezone(timedelta(hours=3))
MAX_POSTED_HISTORY = 500  # how many old story IDs we remember, to avoid re-posting

FEEDS = [
    "https://tsc.go.ke/index.php/media-center/rss.xml",  # adjust to real TSC feed URL
    "https://educationnews.co.ke/feed/",
    "https://www.the-star.co.ke/rss",
    "https://news.google.com/rss/search?q=TSC+Kenya+teachers&hl=en-KE&gl=KE&ceid=KE:en",
]

RELEVANT_KEYWORDS = [
    "tsc", "knut", "kuppet", "cba", "payslip", "teacher recruitment",
    "teacher", "promotion", "salary", "delocalization", "internship",
]

MAJOR_KEYWORDS = {
    "payslip": 3,
    "cba": 3,
    "collective bargaining": 3,
    "promotion": 2,
    "recruitment": 3,
    "salary increment": 3,
    "salary increase": 3,
    "strike": 3,
    "job group": 2,
    "arrears": 2,
    "internship": 2,
    "delocalization": 2,
    "tsc": 1,
    "knut": 1,
    "kuppet": 1,
}
MAJOR_THRESHOLD = 3  # total weighted score needed to count as a "major story"

AGENTS = [
    {
        "name": "TeacherDesk Kenya",
        "voice": "warm and encouraging, like a colleague sharing good news in the staffroom",
    },
    {
        "name": "TSC Watch",
        "voice": "sharp and to-the-point, focused on what changes for teachers practically",
    },
    {
        "name": "Classroom Digest",
        "voice": "conversational and a little playful, while staying respectful of serious topics",
    },
]

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
FB_PAGE_ID = os.environ.get("FB_PAGE_ID")
FB_PAGE_ACCESS_TOKEN = os.environ.get("FB_PAGE_ACCESS_TOKEN")


# --------------------------------------------------------------------------
# SELF-REWRITING STATE (stored inside this .py file, no JSON needed)
# --------------------------------------------------------------------------

_THIS_FILE = os.path.abspath(__file__)


def save_state(next_agent_index, posted_ids):
    """Rewrite the STATE START / STATE END block inside this very file."""
    posted_ids = posted_ids[-MAX_POSTED_HISTORY:]

    with open(_THIS_FILE, "r", encoding="utf-8") as f:
        source = f.read()

    new_block = (
        "# STATE START -- do not hand-edit the layout of this block,\n"
        "# only the values, if you ever need to reset something manually.\n"
        f"NEXT_AGENT_INDEX = {next_agent_index}\n"
        f"POSTED_IDS = {posted_ids!r}\n"
        "# STATE END"
    )

    pattern = re.compile(
        r"# STATE START.*?# STATE END",
        re.DOTALL,
    )
    updated_source = pattern.sub(new_block, source, count=1)

    with open(_THIS_FILE, "w", encoding="utf-8") as f:
        f.write(updated_source)


def story_id(entry):
    key = entry.get("link") or entry.get("title", "")
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# FEED SCANNING + SCORING
# --------------------------------------------------------------------------

def clean_text(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def score_story(title, summary):
    combined = f"{title} {summary}".lower()
    score = 0
    for kw, weight in MAJOR_KEYWORDS.items():
        if kw in combined:
            score += weight
    return score


def is_relevant(title, summary):
    combined = f"{title} {summary}".lower()
    return any(kw in combined for kw in RELEVANT_KEYWORDS)


def fetch_candidates(posted_ids):
    """Return relevant, not-yet-posted candidates sorted by 'major' score desc."""
    candidates = []
    for feed_url in FEEDS:
        try:
            parsed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[warn] could not parse {feed_url}: {e}")
            continue

        for entry in parsed.entries:
            title = clean_text(entry.get("title", ""))
            summary = clean_text(entry.get("summary", "") or entry.get("description", ""))
            if not title:
                continue
            if not is_relevant(title, summary):
                continue

            sid = story_id(entry)
            if sid in posted_ids:
                continue

            score = score_story(title, summary)
            candidates.append({
                "id": sid,
                "title": title,
                "summary": summary,
                "score": score,
            })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates


# --------------------------------------------------------------------------
# PROMPT + LLM CALL
# --------------------------------------------------------------------------

def build_prompt(agent, title, summary):
    return f"""You are the social media editor for "{agent['name']}", a Facebook \
page that keeps Kenyan teachers updated on TSC news, payslips, promotions, \
CBA developments, and recruitment. Your writing voice is {agent['voice']}.

The material below is reference only -- it is NOT something to copy from.

ORIGINAL HEADLINE (reference only, never repeat or quote it):
{title}

FACTS (reference only, never copy sentences from this):
{summary}

Write a brand-new Facebook post for your audience, following ALL of these rules:

1. Write entirely new sentences -- do not reuse wording, structure, or the
   headline from the reference material above.
2. Never mention the source website, journalist, article, or publication date,
   and never say things like "according to reports" or "according to the article".
3. Never use time words: today, yesterday, this morning, this evening, this
   week, or recently. If timing matters, describe it in relative terms tied to
   the news itself (e.g. "the new circular", "the latest CBA phase") instead.
4. Only use information that is directly supported by the FACTS above --
   never invent names, figures, dates, or outcomes. If the facts are limited,
   write a shorter post rather than padding with unsupported detail.
5. Speak directly to the audience using "you", "your", or "Kenyans" naturally.
6. Make the opening sentence genuinely interesting so people want to keep
   reading -- do not open with a generic phrase like "Here is an important
   development."
7. Clearly explain why this matters for teachers reading the page -- what it
   means for their pay, their job, or their day-to-day work.
8. You may end with a natural, low-key invitation for engagement (e.g. asking
   people to share their thoughts or tag a colleague this affects) -- but do
   NOT ask anyone to visit another website or link.
9. Keep the whole post between 40 and 90 words.
10. Do not include a URL or name any source.
11. End naturally -- no abrupt cutoffs, no sign-off, no hashtags unless they
    arise naturally from the content.

Write ONLY the final Facebook post -- no preamble, no notes, no quotation marks."""


def call_llm(prompt):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 400,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text_parts = [block["text"] for block in data.get("content", []) if block.get("type") == "text"]
    return "".join(text_parts).strip()


# --------------------------------------------------------------------------
# FACEBOOK POSTING
# --------------------------------------------------------------------------

def post_to_facebook(message):
    if not FB_PAGE_ID or not FB_PAGE_ACCESS_TOKEN:
        raise RuntimeError("FB_PAGE_ID or FB_PAGE_ACCESS_TOKEN is not set")

    url = f"https://graph.facebook.com/{FB_PAGE_ID}/feed"
    resp = requests.post(url, data={
        "message": message,
        "access_token": FB_PAGE_ACCESS_TOKEN,
    }, timeout=30)

    if resp.status_code != 200:
        print(f"[error] Facebook post failed: {resp.status_code} {resp.text}")
        resp.raise_for_status()

    return resp.json()


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main():
    posted_ids = set(POSTED_IDS)

    candidates = fetch_candidates(posted_ids)
    major = [c for c in candidates if c["score"] >= MAJOR_THRESHOLD]

    if not major:
        print("No major stories found this run. Nothing posted.")
        return

    top_story = major[0]
    print(f"Selected story (score={top_story['score']}): {top_story['title']}")

    agent_index = NEXT_AGENT_INDEX % len(AGENTS)
    agent = AGENTS[agent_index]
    print(f"Agent for this turn: {agent['name']}")

    prompt = build_prompt(agent, top_story["title"], top_story["summary"])
    post_text = call_llm(prompt)
    print("---- Generated post ----")
    print(post_text)
    print("-------------------------")

    post_to_facebook(post_text)
    print("Posted to Facebook successfully.")

    # advance turn, remember this story as posted -- rewrite this file's state block
    new_agent_index = (agent_index + 1) % len(AGENTS)
    new_posted_ids = list(posted_ids) + [top_story["id"]]
    save_state(new_agent_index, new_posted_ids)


if __name__ == "__main__":
    main()
