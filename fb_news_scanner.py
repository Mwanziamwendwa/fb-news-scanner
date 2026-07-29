"""
Kefa's Kenya News Scanner — Full Micro-Agent Edition
Covers: core financial topics, loan-target segments, all national government
ministries, and all 47 Kenyan counties. Each "micro agent" tracks its own
posting history so nothing repeats, and a rotation system spreads posting
across agents fairly over time instead of flooding the Page in one run.

Environment variables required (set as GitHub Secrets):
  PAGE_ACCESS_TOKEN
  PAGE_ID
"""

import os
import json
import time
import requests
import feedparser
from datetime import datetime, timedelta
import pytz

# ---------- GLOBAL CONFIG ----------

PAGE_ID = os.environ["PAGE_ID"]
PAGE_ACCESS_TOKEN = os.environ["PAGE_ACCESS_TOKEN"]
GRAPH_API_VERSION = "v20.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{PAGE_ID}/feed"

NAIROBI_TZ = pytz.timezone("Africa/Nairobi")
POSTED_LOG_FILE = "posted_log.json"
MAX_LOG_SIZE_PER_AGENT = 300
LOOKBACK_HOURS = 24

# Safety cap: total posts across ALL agents in a single run.
# Keeps the Page from posting too much at once (avoids spam flags).
TOTAL_MAX_POSTS_PER_RUN = 10

# How many agents to actively try each run (rotation window).
# Over multiple runs, every agent eventually gets a turn.
AGENTS_PER_RUN = 20


def gnews(query):
    """Build a Google News RSS URL for a given search query."""
    return f"https://news.google.com/rss/search?q={query.replace(' ', '+')}&hl=en-KE&gl=KE&ceid=KE:en"


# ---------- CORE FINANCIAL AGENTS ----------

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

# ---------- LOAN-TARGET SEGMENT AGENTS ----------

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

# ---------- GOVERNMENT MINISTRIES AGENT ----------
# Note: ministry names/portfolios can change after Cabinet reshuffles —
# double check against the current Executive Order if you want to fine-tune.

MINISTRY_NAMES = [
    "Ministry of Interior and National Administration Kenya",
    "Ministry of Defence Kenya",
    "Ministry of Foreign Affairs Kenya",
    "National Treasury Kenya",
    "Ministry of Education Kenya",
    "Ministry of Health Kenya",
    "Ministry of Agriculture Kenya",
    "Ministry of Lands and Housing Kenya",
    "Ministry of Transport Kenya",
    "Ministry of Energy Kenya",
    "Ministry of Trade Investment Industry Kenya",
    "Ministry of ICT Digital Economy Kenya",
    "Ministry of Labour Kenya",
    "Ministry of Water Sanitation Kenya",
    "Ministry of Environment Climate Change Kenya",
    "Ministry of Tourism and Wildlife Kenya",
    "Ministry of Sports Culture Heritage Kenya",
    "Ministry of Cooperatives MSME Kenya",
    "Ministry of Mining Blue Economy Kenya",
    "Ministry of Public Service Gender Kenya",
]

GOVERNMENT_AGENTS = [
    {
        "name": "government_ministries",
        "hashtags": "#KenyaGovernment #Ministries #PublicPolicy",
        "feeds": [gnews(m) for m in MINISTRY_NAMES],
        "keywords": ["ministry", "cabinet secretary", "government", "state department", "policy"],
    }
]

# ---------- COUNTY NEWS AGENT ----------

COUNTY_NAMES = [
    "Mombasa", "Kwale", "Kilifi", "Tana River", "Lamu", "Taita Taveta",
    "Garissa", "Wajir", "Mandera", "Marsabit", "Isiolo", "Meru",
    "Tharaka Nithi", "Embu", "Kitui", "Machakos", "Makueni", "Nyandarua",
    "Nyeri", "Kirinyaga", "Murang'a", "Kiambu", "Turkana", "West Pokot",
    "Samburu", "Trans Nzoia", "Uasin Gishu", "Elgeyo Marakwet", "Nandi",
    "Baringo", "Laikipia", "Nakuru", "Narok", "Kajiado", "Kericho",
    "Bomet", "Kakamega", "Vihiga", "Bungoma", "Busia", "Siaya", "Kisumu",
    "Homa Bay", "Migori", "Kisii", "Nyamira", "Nairobi",
]

GOVERNMENT_AGENTS.append({
    "name": "county_news",
    "hashtags": "#CountyNews #Kenya47Counties #Devolution",
    "feeds": [gnews(f"{c} county Kenya") for c in COUNTY_NAMES],
    "keywords": ["county", "governor", "devolution", "county assembly", "ward"],
})

# ---------- COMBINE ALL AGENTS ----------
# Each agent gets a default max_posts_per_run if not specified individually.

ALL_AGENTS = FINANCE_AGENTS + LOAN_TARGET_AGENTS + GOVERNMENT_AGENTS

for agent in ALL_AGENTS:
    agent.setdefault("max_posts_per_run", 2)

# ---------- SHARED HELPERS ----------

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

def build_post_text(title, hashtags):
    return f"📢 {title}\n\n{hashtags}"

def post_to_facebook(message):
    payload = {"message": message, "access_token": PAGE_ACCESS_TOKEN}
    response = requests.post(GRAPH_URL, data=payload, timeout=30)
    if response.status_code == 200:
        print(f"[OK] Posted: {response.json()}")
        return True
    else:
        print(f"[ERROR] Failed to post: {response.status_code} {response.text}")
        return False

# ---------- MICRO-AGENT RUNNER ----------

def run_agent(agent, full_log, remaining_total):
    name = agent["name"]
    already_posted = set(full_log.get(name, []))
    posts_made = 0
    newly_posted = []
    per_agent_cap = min(agent["max_posts_per_run"], remaining_total)

    if per_agent_cap <= 0:
        return newly_posted, 0

    for feed_url in agent["feeds"]:
        if posts_made >= per_agent_cap:
            break
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"[{name}] [WARN] Could not parse feed {feed_url}: {e}")
            continue

        print(f"[{name}] Feed returned {len(feed.entries)} entries: {feed_url}")

        for entry in feed.entries:
            if posts_made >= per_agent_cap:
                break

            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            summary = entry.get("summary", "")
            published_parsed = entry.get("published_parsed")

            if not title or not link:
                continue
            if title in already_posted:
                continue
            if not is_relevant(title, summary, agent["keywords"]):
                continue
            if not is_recent(published_parsed):
                print(f"[{name}] Skipped (too old): {title}")
                continue

            source_name = extract_source_name(entry)
            message = build_post_text(title, agent["hashtags"])
            success = post_to_facebook(message)

            already_posted.add(title)
            newly_posted.append(title)
            if success:
                posts_made += 1
                time.sleep(5)

    print(f"[{name}] Posts made this run: {posts_made}")
    return newly_posted, posts_made

# ---------- MAIN ----------

def main():
    full_log = load_posted_log()
    rotation_index = full_log.get("_rotation_index", 0)

    # Rotate the agent list so a different slice gets priority each run
    ordered_agents = ALL_AGENTS[rotation_index:] + ALL_AGENTS[:rotation_index]
    agents_to_try = ordered_agents[:AGENTS_PER_RUN]

    remaining_total = TOTAL_MAX_POSTS_PER_RUN

    for agent in agents_to_try:
        if remaining_total <= 0:
            break
        newly_posted, made = run_agent(agent, full_log, remaining_total)
        existing = full_log.get(agent["name"], [])
        full_log[agent["name"]] = (existing + newly_posted)[-MAX_LOG_SIZE_PER_AGENT:]
        remaining_total -= made

    # advance rotation pointer for next run
    new_index = (rotation_index + AGENTS_PER_RUN) % len(ALL_AGENTS)
    full_log["_rotation_index"] = new_index

    save_posted_log(full_log)
    print(f"All agents finished this run. Total agents: {len(ALL_AGENTS)}. "
          f"Posts made: {TOTAL_MAX_POSTS_PER_RUN - remaining_total}")

if __name__ == "__main__":
    main()
