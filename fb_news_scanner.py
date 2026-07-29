feed {feed_url}: {e}")
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
