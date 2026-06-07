# Job Radar

Polls company job boards directly across **5 ATSs** (Greenhouse, Lever, Ashby,
Workday, SmartRecruiters), rules-filters then LLM-scores new postings against my
profile, and pings a Discord channel with color-coded, urgency-tagged embeds.
Runs free on GitHub Actions cron. Polling the ATS APIs directly means new reqs
are seen within minutes of going live, upstream of LinkedIn/Indeed aggregators.

## Quick start (local, dry-run)

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                 # run tests
python scripts/smoke.py   # hit a few real boards
python -m job_radar       # full run; prints to console (dry-run, no secrets needed)
```

## Going live

1. Create a Discord channel + webhook; copy the webhook URL.
2. Get a Gemini API key (free tier).
3. Set GitHub Action secrets: `DISCORD_WEBHOOK_URL`, `LLM_API_KEY`, optional `DISCORD_ROLE_ID`.
4. The `.github/workflows/radar.yml` cron runs every ~15 min and persists the
   seen-set via Actions cache.

## Config

- `config/profile.yaml` — what counts as relevant to me (roles, locations, thresholds, LLM summary).
- `config/companies.yaml` — the watch-list (slug + ats + tier; Workday entries also need `wd_host` + `wd_site`). Grow it freely; bad slugs are skipped.

Bulk-add companies from a list (`slug,ats[,tier]` per line, dedups automatically):

```bash
python -m job_radar.seed path/to/list.csv      # merges into config/companies.yaml
```

This is how you scale the watch-list from dozens to thousands using public ATS
slug dumps. Coverage is the dial: more companies = more first-dibs reach.

## How it works

`sources` fetch all boards concurrently -> `dedup` drops already-seen -> `filters`
free rules pre-filter -> `scorer` LLM-scores survivors -> `urgency` classifies ->
`notify` posts to Discord (or console in dry-run). See `docs/superpowers/`.
