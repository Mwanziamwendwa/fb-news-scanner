# Privacy & Secrets Policy

This repository is **public**. Anything committed to it — code, commit history, workflow logs, issues — can be seen by anyone on the internet, forever (even if you delete it later, old commits can still be dug up). This file lists what must never appear in the repo itself, and where it should live instead.

## 1. Never commit these

- Facebook Page Access Token (System User token)
- Meta App Secret (App ID `2255209815297714`'s secret key)
- Page ID (`FACEBOOK_PAGE_ID`) — low risk alone, but keep it out of code anyway, use a secret
- Any `.env` file
- Any file named `token.txt`, `credentials.json`, `secrets.json`, etc.

**Where they go instead:** GitHub repo → Settings → Secrets and variables → Actions → New repository secret. The workflow reads them as `${{ secrets.FACEBOOK_PAGE_TOKEN }}` etc. Secrets are encrypted and never shown in logs, even to you, once saved.

## 2. `.gitignore` — add this if it's not already there

```
.env
*.env
secrets.json
credentials.json
token.txt
__pycache__/
*.pyc
```

## 3. If a secret ever gets committed by accident

Removing the file in a new commit is **not enough** — it's still in the git history. If this happens:

1. Immediately revoke/regenerate the token in Meta Business Manager (System Users → your user → Generate New Token).
2. Update the GitHub Secret with the new token.
3. Optionally scrub history with `git filter-repo` or BFG Repo-Cleaner — but regenerating the token matters more than scrubbing, since the old one is dead either way.

## 4. What's safe to keep in the repo (public, by design)

- `news_scanner.py` and workflow YAML — no secrets embedded, only `secrets.` references
- `seen.json` / dedupe logs — story IDs and headlines only, nothing sensitive
- `suggested_posts.md` — draft post text, not sensitive
- README, this file

## 5. GitHub Actions logs

Logs are visible to anyone if the repo is public and the workflow runs on `push`/`schedule` (not on a fork's PR, which GitHub restricts). Double-check that `print()` / `echo` statements in the script never log the token itself, even partially (e.g. no `print(f"Using token: {token}")`).

## 6. Quick checklist before every push

- [ ] No token, App Secret, or `.env` in the diff
- [ ] Secrets are referenced via `secrets.NAME`, not hardcoded
- [ ] `.gitignore` covers local credential files
- [ ] Add privacy policy
