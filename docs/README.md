# Job Radar

Polls company job boards directly across **5 ATSs** (Greenhouse, Lever, Ashby,
Workday, SmartRecruiters), rules-filters then LLM-scores new postings against my
profile, and pings a Discord channel with color-coded, urgency-tagged embeds.
Runs free on GitHub Actions cron. Polling the ATS APIs directly means new reqs
are seen within minutes of going live, **upstream of LinkedIn/Indeed aggregators**.

Currently watching **300+ live boards** across big tech, fintech, and a deep
bench of niche/YC startups (AI infra, dev tools, robotics). The live
watch-list and scoring profile are **private config** (they encode my
personal targeting); `config/companies.example.yaml` and
`config/profile.example.yaml` show the shape, and CI fetches the real ones
at runtime from a private repo. Grow your own list with `seed` + `validate`;
dead slugs are skipped, and newly-added boards prime silently (no flood).

## Quick start (local, no secrets needed)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                       # run the test suite
cp config/companies.example.yaml config/companies.yaml   # then make them yours
cp config/profile.example.yaml config/profile.yaml
python scripts/smoke.py         # hit one real board per ATS
python -m job_radar --preview --company openai   # see what would surface from one board
python -m job_radar --dry-run --limit 5          # full pipeline, console output, 5 boards
```

## CLI (`python -m job_radar`)

| Flag | What it does |
|---|---|
| `--preview` | Show what would surface from the **current backlog**, ranked by fit. Read-only (no priming, no state writes). Best for tuning `profile.yaml`. |
| `--dry-run` | Run the full pipeline but print to console instead of posting to Discord. |
| `--prime` | Mark everything seen without notifying (re-prime; e.g. after broadening the profile). |
| `--backfill` | One-time: score the current open backlog and write strong matches to the Sheet (no pings, no state change). Seeds the tracker with today's inventory. Reaches back `--backfill-days` (default 60, wider than the cron) and writes only `--backfill-min-fit` and up (default 60). Needs the Sheet env vars; works without an LLM key (heuristic). |
| `--company SLUG` | Only poll one company (local testing). |
| `--limit N` | Only poll the first N companies. |
| `--profile / --companies / --state PATH` | Override config/state paths. |

## Tools

```bash
python -m job_radar.seed <list.csv>      # bulk-add companies (dedups). See format below.
python -m job_radar.validate             # check every board live; report ok/empty/dead
python -m job_radar.validate --prune     # ...and drop dead slugs from companies.yaml
```

Seed CSV format (one per line; `#` comments):
```
slug,ats[,tier]                       # greenhouse | lever | ashby | smartrecruiters
slug,workday,tier,wd_host,wd_site     # workday needs the tenant host + site
```

## Config

- `config/profile.yaml` — what counts as relevant to me (roles, locations, thresholds,
  and the free-text summary the LLM scores against). Tune with `--preview`.
- `config/companies.yaml` — the watch-list. `tier: dream` auto-pings (bypasses the LLM);
  reserve it for high-fit companies. `tier: target` lets the LLM filter. Banks are
  `target` so their broad co-op pools get narrowed to SWE/AI/Data.

Finding a **Workday** tenant: open the company's careers page and watch the network
request to `/wday/cxs/<tenant>/<site>/jobs`. The host is the `wdN` part
(e.g. `cibc.wd3.myworkdayjobs.com` → `wd_host: wd3`, `wd_site: campus`).

## Going live (free, GitHub Actions)

1. Create a Discord channel + webhook; copy the URL.
2. Get an LLM key. Default scorer is **Gemini Flash** (free tier, `gemini-2.5-flash`).
   For **Claude** (Anthropic API), set `LLM_PROVIDER=claude` (defaults to Haiku 4.5).
   For **AWS Bedrock** (no per-day free-tier cap, best if you have AWS): `pip install
   -e ".[bedrock]"`, set `LLM_PROVIDER=bedrock`, add `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` / `AWS_REGION` secrets, and set `LLM_MODEL` to your enabled
   Bedrock model id (e.g. `anthropic.claude-3-5-haiku-20241022-v1:0`, or a `us.`/`eu.`
   inference-profile variant for your region). Any scorer falls back to a free
   deterministic heuristic if it errors, so a quota blip never drops coverage.
3. `gh repo create job-radar --private --source=. --remote=origin --push`
4. Repo Settings → Secrets → Actions: `DISCORD_WEBHOOK_URL`, `LLM_API_KEY`,
   optional `DISCORD_ROLE_ID`, `LLM_PROVIDER`.
5. Trigger once via the Actions tab (confirm a `PRIMED` run — the first run primes
   silently so you aren't flooded), then the ~15-min cron takes over.

## Tracker (Google Sheet + reminders, no server)

The cron doubles as an application tracker. When the Sheet secrets are present, each
poll mirrors every match into a Google Sheet (one `New` row per job), and a second
**daily** workflow (`remind.yml`) reads the Sheet and pings Discord with a catch-up:
how many roles are pending and how long since you last applied, then **must-apply**
(rows you flagged high priority), **due soon** (deadline within 3 days), and strong
roles still unapplied. You triage in the Sheet — set `Priority` (`must`/`high`/`med`/
`low`), fill `Deadline`, mark `Status` Applied/Skip — and reminders stop nagging a row
once it leaves `New`. So a busy stretch just means the backlog waits for you, sorted
by what you said matters. No always-on process: it all rides the free Actions cron.

Two extra Action secrets turn it on (the poll skips the Sheet cleanly if they're absent):
- `GOOGLE_SHEET_ID` — from the Sheet URL, the part between `/d/` and `/edit`.
- `GOOGLE_CREDENTIALS` — the full service-account JSON key (paste the file contents).

Google setup: enable the Sheets API, create a **service account**, download its JSON
key, and share the Sheet with the service-account email (Editor). The Sheet's columns
are created automatically on first write. Locally you can preview reminders with
`GOOGLE_SHEET_ID=... GOOGLE_CREDENTIALS_PATH=google-creds.json python -m job_radar.remind`.

The Sheet otherwise fills only as *new* roles appear (everything already seen was
primed). To seed it with today's open inventory immediately, run `--backfill` once
(scores the current backlog and writes matches; no pings, no state change). Mind the
LLM quota: the Gemini free tier is ~200 requests/day shared with the cron, so the
backfill is capped (raise `BACKFILL_CAP` with a paid key).

## Optional: always-on interactive bot

If you'd rather press **Applied / Not for me** buttons and use slash commands
(`/pending` `/top` `/due` `/stats`) instead of editing the Sheet, run the bot as a
persistent process (a VM or a machine that stays on). Same Sheet, plus Discord
buttons. Design: `docs/superpowers/specs/2026-06-07-tracker-bot-design.md`.

```bash
pip install -e ".[bot]"
python -m job_radar.bot          # or: python -m job_radar.bot --check  (test the Sheet only)
```

Put the values in a git-ignored `.env`: `DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`,
`GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_PATH=google-creds.json`, `LLM_API_KEY`.
Bot invite scopes: `bot` + `applications.commands`; permissions: Send Messages +
Embed Links (Guild Install).

## How it works

`sources` fetch all boards concurrently → `dedup` drops already-seen (and primes
silently on first run, and per-board when you add a new company, so neither floods)
→ `filters` free rules pre-filter → `scorer` LLM-scores
survivors (Gemini/Claude, enriching Workday/SmartRecruiter descriptions first; a
deterministic heuristic transparently covers any posting the LLM can't score, e.g.
on a rate limit, so an outage degrades coverage instead of dropping it) →
`urgency` classifies (🔴 fresh+high or dream / 🟡 relevant / 🟢 weak→digest) →
`notify` posts to Discord. State persists in the Actions cache; a weekly keepalive
keeps the cron from auto-disabling. Design docs in `docs/superpowers/`.
```
