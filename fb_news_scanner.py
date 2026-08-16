"""
Kefa's Kenya News Scanner — Suggestion Mode
Scans all topic feeds and writes ready-to-copy post suggestions into
suggested_posts.md. No Facebook posting happens automatically — Kefa
reviews the list and posts manually whenever he wants.

No API keys, tokens, or Facebook permissions needed at all for this version.
"""

import os
import json
import re
from datetime import datetime, timedelta
import pytz
import feedparser

NAIROBI_TZ = pytz.timezone("Africa/Nairobi")
POSTED_LOG_FILE = "posted_log.json"
SUGGESTIONS_FILE = "suggested_posts.md"
MAX_LOG_SIZE_PER_AGENT = 300
LOOKBACK_HOURS = 24
MAX_SUGGESTIONS_PER_AGENT = 3

def strip_html(text):
    clean = re.sub(r"<[^>]+>", " ", text or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean

def truncate_words(text, max_words=100):
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]).rstrip(",.;:") + "..."

def extract_clean_text(summary_raw):
    text = strip_html(summary_raw)
    if not text:
        return ""
    return truncate_words(text, max_words=100)


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

# ---------- DIRECT OUTLET FEEDS (faster than Google News aggregation) ----------

GENERAL_KENYA_AGENT = [
    {
        "name": "breaking_kenya_general",
        "hashtags": "#KenyaNews #BreakingNews",
        "feeds": [
            "https://ntvkenya.co.ke/feed/",
            "https://www.the-star.co.ke/rss/",
            "https://www.standardmedia.co.ke/rss/headlines.php",
            "https://www.citizen.digital/feed",
            "https://www.capitalfm.co.ke/news/feed/",
            "https://www.businessdailyafrica.com/bd/rss",
            "https://www.kbc.co.ke/feed/",
            "https://www.tuko.co.ke/rss/",
        ],
        # broad keyword net since this agent covers general/breaking news
        "keywords": ["kenya", "nairobi", "county", "government", "president", "parliament",
                      "school", "hospital", "police", "court", "election", "business",
                      "economy", "county", "cabinet", "governor", "national"],
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

# ---------- MAIN ----------

def main():
    full_log = load_posted_log()
    suggestions_by_agent = {}

    for agent in ALL_AGENTS:
        name = agent["name"]
        already_suggested = set(full_log.get(name, []))
        found = []       # titles only, for the log (dedup tracking)
        found_items = [] # (title, summary) pairs, for building the post text

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
                if title in already_suggested:
                    continue
                if not is_relevant(title, summary, agent["keywords"]):
                    continue
                if not is_recent(published_parsed):
                    continue

                found.append(title)
                found_items.append((title, summary))
                already_suggested.add(title)

        if found_items:
            suggestions_by_agent[name] = {
                "hashtags": agent["hashtags"],
                "items": found_items,
            }

        existing = full_log.get(name, [])
        full_log[name] = (existing + found)[-MAX_LOG_SIZE_PER_AGENT:]

    save_posted_log(full_log)

    # Write the suggestions file, organized into suggested posting time slots
    time_slots = ["7:00 AM", "9:00 AM", "11:00 AM", "1:00 PM",
                  "3:00 PM", "5:00 PM", "7:00 PM", "9:00 PM"]

    with open(SUGGESTIONS_FILE, "w", encoding="utf-8") as f:
        f.write(f"# 📋 Suggested Posts — updated {nairobi_time_label()}\n\n")
        f.write("Copy any of these into Facebook manually at the suggested time (Nairobi time), or whenever suits you. Newest scan at the top.\n\n---\n\n")

        if not suggestions_by_agent:
            f.write("No new relevant stories found this run. Check back next run.\n")
        else:
            # Flatten all suggestions into one list, then assign time slots round-robin
            all_items = []
            for name, data in suggestions_by_agent.items():
                for title, summary in data["items"]:
                    all_items.append((name, title, summary, data["hashtags"]))

            for i, (name, title, summary, hashtags) in enumerate(all_items):
                slot = time_slots[i % len(time_slots)]
                extract = extract_clean_text(summary)

                f.write(f"## 🕒 {slot} — {name.replace('_', ' ').title()}\n\n")
                f.write(f"**Topic:** {title}\n\n")
                if extract:
                    f.write(f"**Raw text (~100 words):**\n{extract}\n\n")
                f.write(f"**Hashtags:** {hashtags}\n\n")
                f.write("_Paste the Topic + Raw text above to Claude to get a rewritten, engaging version, then post to Facebook._\n\n")
                f.write("---\n\n")

    total = sum(len(v["items"]) for v in suggestions_by_agent.values())
    print(f"Done. {total} new post suggestions written to {SUGGESTIONS_FILE}")

if __name__ == "__main__":
    main()
