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
                continue

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

    new_index = (rotation_index + AGENTS_PER_RUN) % len(ALL_AGENTS)
    full_log["_rotation_index"] = new_index

    save_posted_log(full_log)
    print(f"All agents finished this run. Total agents: {len(ALL_AGENTS)}. "
          f"Posts made: {TOTAL_MAX_POSTS_PER_RUN - remaining_total}")

if name == "main":
    main()
