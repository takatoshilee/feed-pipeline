# Job Radar

Polls company job boards directly across **5 ATSs** (Greenhouse, Lever, Ashby,
Workday, SmartRecruiters), rules-filters then LLM-scores new postings against my
profile, and pings a Discord channel with color-coded, urgency-tagged embeds.
Runs free on GitHub Actions cron. Polling the ATS APIs directly means new reqs
are seen within minutes of going live, **upstream of LinkedIn/Indeed aggregators**.

Currently watching **200+ live boards** (incl. RBC/CIBC/BMO/Sun Life/TD early-talent
boards for Canadian co-op recruiting, plus big tech, fintech, and AI/dev-tool startups).
Grow the list freely with `seed` + `validate`; dead slugs are skipped.

## Quick start (local, no secrets needed)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                       # run the test suite
python scripts/smoke.py         # hit one real board per ATS
python -m job_radar --preview --company cibc   # see what would surface from one board
python -m job_radar --dry-run --limit 5        # full pipeline, console output, 5 boards
```

## CLI (`python -m job_radar`)

| Flag | What it does |
|---|---|
| `--preview` | Show what would surface from the **current backlog**, ranked by fit. Read-only (no priming, no state writes). Best for tuning `profile.yaml`. |
| `--dry-run` | Run the full pipeline but print to console instead of posting to Discord. |
| `--prime` | Mark everything seen without notifying (re-prime; e.g. after broadening the profile). |
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
2. Get an LLM key. Default scorer is **Gemini Flash** (free tier). For **Claude**,
   set `LLM_PROVIDER=claude` (defaults to Haiku 4.5; override with `LLM_MODEL`).
3. `gh repo create job-radar --private --source=. --remote=origin --push`
4. Repo Settings → Secrets → Actions: `DISCORD_WEBHOOK_URL`, `LLM_API_KEY`,
   optional `DISCORD_ROLE_ID`, `LLM_PROVIDER`.
5. Trigger once via the Actions tab (confirm a `PRIMED` run — the first run primes
   silently so you aren't flooded), then the ~15-min cron takes over.

## How it works

`sources` fetch all boards concurrently → `dedup` drops already-seen (and primes
silently on first run) → `filters` free rules pre-filter → `scorer` LLM-scores
survivors (Gemini/Claude, enriching Workday/SmartRecruiter descriptions first) →
`urgency` classifies (🔴 fresh+high or dream / 🟡 relevant / 🟢 weak→digest) →
`notify` posts to Discord. State persists in the Actions cache; a weekly keepalive
keeps the cron from auto-disabling. Design docs in `docs/superpowers/`.
```
