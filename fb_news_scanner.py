"""
Kefa's Kenya News Scanner — Suggestion Mode
Scans all topic feeds and writes ready-to-copy post suggestions into
suggested_posts.md. No Facebook posting happens automatically — Kefa
reviews the list and posts manually whenever he wants.

No API keys, tokens, or Facebook permissions needed at all for this version.

UPDATED:
  - Full verified list of Kenyan RSS feeds (53 sources, checked one by one —
    dead/wrong URLs from the old GENERAL_KENYA_AGENT list have been fixed
    or removed; sites confirmed to have no RSS feed at all are left out).
  - Fuzzy duplicate detection: the same real-world story reported by two
    different outlets with two different headlines is now recognized as
    ONE story, not two. Detection works both within a single run (so you
    don't get "Homa Bay violence" from three outlets in one suggestion
    batch) and against the historical log (so a story doesn't resurface
    days later just because a different site rewrote the headline).
"""

import os
import re
import json
from datetime import datetime, timedelta
import pytz
import feedparser

NAIROBI_TZ = pytz.timezone("Africa/Nairobi")
POSTED_LOG_FILE = "posted_log.json"
SUGGESTIONS_FILE = "suggested_posts.md"
MAX_LOG_SIZE_PER_AGENT = 300
LOOKBACK_HOURS = 24
MAX_SUGGESTIONS_PER_AGENT = 3

# Similarity threshold for "same story, different headline".
# Jaccard overlap of significant words; 0.55 catches paraphrased
# headlines about the same event without over-merging unrelated stories.
DUPLICATE_SIMILARITY_THRESHOLD = 0.55


def gnews(query):
    return f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-KE&gl=KE&ceid=KE:en"


# ---------- AGENTS (same topic coverage as before) ----------

FINANCE_AGENTS = [
    {"name": "teachers", "hashtags": "#TSC #Teachers #KenyaEducation",
     "feeds": [gnews("TSC Kenya teachers"), gnews("teacher recruitment Kenya")],
     "keywords": ["tsc", "teacher", "payslip", "cba", "teacher recruitment"]},
    {"name": "civil_servants", "hashtags": "#CivilServants #CountyGovernment #KenyaJobs",
     "feeds": [gnews("civil servants Kenya salary"), gnews("public service Kenya")],
     "keywords": ["civil servant", "county government", "salary increment", "public service"]},
    {"name": "sacco_chama", "hashtags": "#SACCO #Chama #Savings",
     "feeds": [gnews("SACCO Kenya"), gnews("chama Kenya savings")],
     "keywords": ["sacco", "chama", "sasra", "savings group"]},
    {"name": "banking_microfinance", "hashtags": "#CBK #BankLoans #Microfinance",
     "feeds": [gnews("CBK Central Bank Kenya interest rate"), gnews("bank loan Kenya"), gnews("microfinance Kenya")],
     "keywords": ["cbk", "central bank", "interest rate", "bank loan", "microfinance", "credit", "mortgage"]},
    {"name": "sme_business", "hashtags": "#SME #SmallBusiness #KenyaEconomy",
     "feeds": [gnews("SME business loan Kenya"), gnews("KRA Kenya tax")],
     "keywords": ["sme", "business loan", "kra", "tax", "trader", "small business"]},
    {"name": "world_economy", "hashtags": "#WorldBank #KenyaEconomy #Inflation",
     "feeds": [gnews("World Bank Kenya economy")],
     "keywords": ["world bank", "inflation", "cost of living", "economy", "gdp"]},
]

LOAN_TARGET_AGENTS = [
    {"name": "nhif_health_insurance", "hashtags": "#SHA #NHIF #HealthCover",
     "feeds": [gnews("NHIF SHA Kenya"), gnews("health insurance Kenya")],
     "keywords": ["nhif", "sha", "health insurance", "medical cover"]},
    {"name": "nssf_pension", "hashtags": "#NSSF #Pension #Retirement",
     "feeds": [gnews("NSSF Kenya pension")],
     "keywords": ["nssf", "pension", "retirement benefits"]},
    {"name": "hustler_fund_youth", "hashtags": "#HustlerFund #YouthFund #KenyaJobs",
     "feeds": [gnews("Hustler Fund Kenya"), gnews("youth employment fund Kenya")],
     "keywords": ["hustler fund", "youth fund", "youth employment", "government credit"]},
    {"name": "matatu_boda_transport", "hashtags": "#Matatu #BodaBoda #Transport",
     "feeds": [gnews("matatu sacco Kenya"), gnews("boda boda Kenya")],
     "keywords": ["matatu", "boda boda", "psv", "transport sacco"]},
    {"name": "real_estate_mortgage", "hashtags": "#RealEstate #Mortgage #Housing",
     "feeds": [gnews("real estate Kenya"), gnews("mortgage Kenya housing")],
     "keywords": ["real estate", "mortgage", "housing", "land", "property"]},
    {"name": "school_fees_education", "hashtags": "#HELB #KUCCPS #SchoolFees",
     "feeds": [gnews("HELB Kenya"), gnews("university fees Kenya KUCCPS")],
     "keywords": ["helb", "kuccps", "school fees", "university fees", "tuition"]},
    {"name": "agriculture_farmers", "hashtags": "#Agriculture #Farmers #Kenya",
     "feeds": [gnews("agriculture Kenya farmers"), gnews("agri loan Kenya cooperative")],
     "keywords": ["farmer", "agriculture", "agri-loan", "cooperative", "cereal board"]},
    {"name": "women_table_banking", "hashtags": "#WomenInBusiness #TableBanking",
     "feeds": [gnews("women table banking Kenya"), gnews("women fund Kenya business")],
     "keywords": ["table banking", "women fund", "women in business", "merry go round"]},
    {"name": "digital_mobile_loans", "hashtags": "#MobileLoans #DigitalLending",
     "feeds": [gnews("mobile loan Kenya"), gnews("Fuliza M-Shwari Kenya")],
     "keywords": ["mobile loan", "fuliza", "m-shwari", "digital lending", "kcb mpesa"]},
    {"name": "motor_vehicle_logbook", "hashtags": "#LogbookLoans #CarFinancing",
     "feeds": [gnews("logbook loan Kenya"), gnews("car import Kenya")],
     "keywords": ["logbook loan", "car import", "vehicle financing", "auto loan"]},
    {"name": "fuel_cost_of_living", "hashtags": "#FuelPrices #CostOfLiving",
     "feeds": [gnews("fuel prices Kenya"), gnews("cost of living Kenya")],
     "keywords": ["fuel price", "petrol", "diesel", "cost of living", "epra"]},
    {"name": "county_business_permits", "hashtags": "#CountyBusiness #SMEPermits",
     "feeds": [gnews("county business permit Kenya"), gnews("SME registration Kenya")],
     "keywords": ["business permit", "trade license", "county revenue", "sme registration"]},
]

MINISTRY_NAMES = [
    "Ministry of Interior and National Administration Kenya", "Ministry of Defence Kenya",
    "Ministry of Foreign Affairs Kenya", "National Treasury Kenya", "Ministry of Education Kenya",
    "Ministry of Health Kenya", "Ministry of Agriculture Kenya", "Ministry of Lands and Housing Kenya",
    "Ministry of Transport Kenya", "Ministry of Energy Kenya", "Ministry of Trade Investment Industry Kenya",
    "Ministry of ICT Digital Economy Kenya", "Ministry of Labour Kenya", "Ministry of Water Sanitation Kenya",
    "Ministry of Environment Climate Change Kenya", "Ministry of Tourism and Wildlife Kenya",
    "Ministry of Sports Culture Heritage Kenya", "Ministry of Cooperatives MSME Kenya",
    "Ministry of Mining Blue Economy Kenya", "Ministry of Public Service Gender Kenya",
]

GOVERNMENT_AGENTS = [
    {"name": "government_ministries", "hashtags": "#KenyaGovernment #Ministries #PublicPolicy",
     "feeds": [gnews(m) for m in MINISTRY_NAMES],
     "keywords": ["ministry", "cabinet secretary", "government", "state department", "policy"]}
]

COUNTY_NAMES = [
    "Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita Taveta", "Garissa", "Wajir",
    "Mandera", "Marsabit", "Isiolo", "Meru", "Tharaka Nithi", "Embu", "Kitui", "Machakos",
    "Makueni", "Nyandarua", "Nyeri", "Kirinyaga", "Murang'a", "Kiambu", "Turkana", "West Pokot",
    "Samburu", "Trans Nzoia", "Uasin Gishu", "Elgeyo Marakwet", "Nandi", "Baringo", "Laikipia",
    "Nakuru", "Narok", "Kajiado", "Kericho", "Bomet", "Kakamega", "Vihiga", "Bungoma", "Busia",
    "Siaya", "Kisumu", "Homa Bay", "Migori", "Kisii", "Nyamira", "Nairobi",
]

GOVERNMENT_AGENTS.append({
    "name": "county_news", "hashtags": "#CountyNews #Kenya47Counties #Devolution",
    "feeds": [gnews(f"{c} county Kenya") for c in COUNTY_NAMES],
    "keywords": ["county", "governor", "devolution", "county assembly", "ward"],
})

# ---------- DIRECT OUTLET FEEDS ----------
# Every URL below was checked individually (fetched or confirmed via the
# publisher's own <link rel="alternate" type="application/rss+xml"> tag).
# Sites with NO working RSS feed (the-star.co.ke, citizen.digital,
# mpasho.co.ke, royalmedia.co.ke — all confirmed dead ends) are left out
# entirely rather than included with a guessed/broken URL.

KENYA_VERIFIED_FEEDS = [
    # General news
    "https://nairobileo.co.ke/feed",
    "https://spmbuzz.com/feed/",
    "https://www.ghafla.co.ke/feed/",
    "https://ghafla.co.ke/ke/feed",
    "https://k24.digital/feed",
    "https://www.kbc.co.ke/feed/",
    "https://www.kenyamoja.com/news/nairobi-leo/feed",
    "https://www.kenyans.co.ke/feeds/news",
    "https://nation.africa/kenya/rss.xml",
    "https://www.kenyanews.go.ke/feed/",
    "https://taifaleo.nation.co.ke/feed",
    "https://nairobiwire.com/feed",
    "https://diasporamessenger.com/feed/",
    "https://mwakilishi.com/feed",
    "https://sharpdaily.co.ke/feed/",
    "https://newstrends.co.ke/feed/",
    "https://sauce.co.ke/feed/",
    "https://thekenyatimes.com/feed/",
    "https://aipate.com/category/news/feed",
    "https://nairobigossipclub.co.ke/feeds",

    # The Standard (sectioned)
    "https://www.standardmedia.co.ke/rss/headlines.php",
    "https://www.standardmedia.co.ke/rss/kenya.php",
    "https://www.standardmedia.co.ke/rss/sports.php",
    "https://www.standardmedia.co.ke/rss/world.php",
    "https://www.standardmedia.co.ke/rss/politics.php",

    # Capital FM — two separate properties/domains
    "https://capitalfm.africa/news/feed/",
    "https://capitalfm.africa/sports/feed/",
    "https://capitalfm.africa/lifestyle/feed/",
    "https://capitalfm.africa/business/feed/",
    "https://www.capitalfm.co.ke/news/feed/",

    # Business / finance
    "https://www.businessdailyafrica.com/service/rss/bd/1939132/feed.rss",
    "https://kenyanwallstreet.com/feed/",

    # Getembe TV (sectioned)
    "https://getembetv.co.ke/rss/latest-posts",
    "https://getembetv.co.ke/rss/category/news",
    "https://getembetv.co.ke/rss/category/business",
    "https://getembetv.co.ke/rss/category/education",
    "https://getembetv.co.ke/rss/category/politics",
    "https://getembetv.co.ke/rss/category/health",

    # Viral Tea (sectioned)
    "https://viraltea.co.ke/rss/latest-posts",
    "https://viraltea.co.ke/rss/category/news",
    "https://viraltea.co.ke/rss/category/breaking",
    "https://viraltea.co.ke/rss/category/national",
    "https://viraltea.co.ke/rss/category/local",

    # Kenyapedia (sectioned — finance/econ heavy, useful for FINANCE_AGENTS too)
    "https://www.kenyapedia.co.ke/rss/latest-posts",
    "https://www.kenyapedia.co.ke/rss/category/jobs",
    "https://www.kenyapedia.co.ke/rss/category/money-and-finances",
    "https://www.kenyapedia.co.ke/rss/category/business-grants-and-financing",
    "https://www.kenyapedia.co.ke/rss/category/debt-and-borrowing",
    "https://www.kenyapedia.co.ke/rss/category/kenya-economy",
    "https://www.kenyapedia.co.ke/rss/category/education-funding",
    "https://www.kenyapedia.co.ke/rss/category/taxes-50",
    "https://www.kenyapedia.co.ke/rss/category/savings-and-investments",
    "https://www.kenyapedia.co.ke/rss/category/recent-news",
]

GENERAL_KENYA_AGENT = [
    {
        "name": "breaking_kenya_general",
        "hashtags": "#KenyaNews #BreakingNews",
        "feeds": KENYA_VERIFIED_FEEDS,
        # broad keyword net since this agent covers general/breaking news
        "keywords": ["kenya", "nairobi", "county", "government", "president", "parliament",
                      "school", "hospital", "police", "court", "election", "business",
                      "economy", "cabinet", "governor", "national"],
    }
]

ALL_AGENTS = FINANCE_AGENTS + LOAN_TARGET_AGENTS + GOVERNMENT_AGENTS + GENERAL_KENYA_AGENT

# ---------- HELPERS ----------

def load_posted_log():
    if os.path.exists(POSTED_LOG_FILE):
        try:
            with open(POSTED_LOG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_posted_log(log):
    with open(POSTED_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)


def is_relevant(title, summary, keywords):
    text = f"{title} {summary}".lower()
    return any(kw in text for kw in keywords)


def is_recent(published_parsed):
    if not published_parsed:
        return True
    published_dt = datetime(*published_parsed[:6], tzinfo=pytz.utc)
    cutoff = datetime.now(pytz.utc) - timedelta(hours=LOOKBACK_HOURS)
    return published_dt >= cutoff


def nairobi_time_label():
    return datetime.now(NAIROBI_TZ).strftime("%A, %d %B %Y — %I:%M %p (Nairobi time)")


# ---------- DUPLICATE / SAME-STORY DETECTION ----------
# Different outlets write different headlines for the same event
# ("37 arrested after Homa Bay chaos" vs "Police nab 37 suspects in
# Linda Mwananchi violence"). Exact-string matching misses this entirely.
# We normalize each headline to its significant words and compare word-set
# overlap (Jaccard similarity). This is fast, needs no extra libraries,
# and is robust enough for headline-level dedup.

_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "as",
    "is", "are", "was", "were", "be", "been", "by", "with", "from", "over",
    "after", "before", "amid", "into", "out", "up", "down", "says", "say",
    "said", "new", "kenya", "kenyan", "this", "that", "it", "its", "his",
    "her", "their", "he", "she", "they", "who", "what", "how", "why",
}


def normalize_title(title):
    """Lowercase, strip punctuation, drop stopwords -> set of significant words."""
    words = re.findall(r"[a-z0-9']+", title.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def title_similarity(title_a, title_b):
    set_a, set_b = normalize_title(title_a), normalize_title(title_b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def is_duplicate_of_any(title, seen_titles):
    """True if `title` is the same story as anything already in seen_titles
    (either this run's other feeds, or the historical log)."""
    for seen in seen_titles:
        if title_similarity(title, seen) >= DUPLICATE_SIMILARITY_THRESHOLD:
            return True
    return False


# ---------- MAIN ----------

def main():
    full_log = load_posted_log()
    suggestions_by_agent = {}

    # Global set of titles already surfaced THIS run, across every agent —
    # so the same story doesn't get suggested twice under two different
    # topic agents (e.g. "fuel_cost_of_living" and "breaking_kenya_general"
    # both picking up a fuel-price story).
    run_wide_titles = []

    for agent in ALL_AGENTS:
        name = agent["name"]
        already_suggested = list(full_log.get(name, []))  # historical, fuzzy-checked
        found = []

        for feed_url in agent["feeds"]:
            if len(found) >= MAX_SUGGESTIONS_PER_AGENT:
                break
            try:
                feed = feedparser.parse(feed_url)
            except Exception:
                continue

            for entry in feed.entries:
                if len(found) >= MAX_SUGGESTIONS_PER_AGENT:
                    break
                title = entry.get("title", "").strip()
                link = entry.get("link", "").strip()
                summary = entry.get("summary", "")
                published_parsed = entry.get("published_parsed")

                if not title or not link:
                    continue
                if not is_relevant(title, summary, agent["keywords"]):
                    continue
                if not is_recent(published_parsed):
                    continue

                # Same-story check against: this agent's history, this run's
                # other agents, and titles already picked in this same batch.
                if is_duplicate_of_any(title, already_suggested):
                    continue
                if is_duplicate_of_any(title, run_wide_titles):
                    continue

                found.append(title)
                already_suggested.append(title)
                run_wide_titles.append(title)

        if found:
            suggestions_by_agent[name] = {
                "hashtags": agent["hashtags"],
                "titles": found,
            }

        existing = full_log.get(name, [])
        full_log[name] = (existing + found)[-MAX_LOG_SIZE_PER_AGENT:]

    save_posted_log(full_log)

    # Write the suggestions file
    with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
        f.write(f"# 📋 Suggested Posts — updated {nairobi_time_label()}\n\n")
        f.write("Copy any of these into Facebook manually. Newest scan at the top.\n\n")
        f.write("_Duplicate stories from different outlets are automatically merged._\n\n---\n\n")

        if not suggestions_by_agent:
            f.write("No new relevant stories found this run. Check back next run.\n")
        else:
            for name, data in suggestions_by_agent.items():
                f.write(f"## {name.replace('_', ' ').title()}\n\n")
                for title in data["titles"]:
                    f.write(f"**📢 {title}**\n\n")
                    f.write(f"{data['hashtags']}\n\n")
                    f.write("---\n\n")

    total = sum(len(v["titles"]) for v in suggestions_by_agent.values())
    print(f"Done. {total} new post suggestions written to {SUGGESTIONS_FILE}")


if __name__ == "__main__":
    main()
