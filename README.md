# Job Radar

Polls Greenhouse / Lever / Ashby company job boards directly, rules-filters then
LLM-scores new postings against my profile, and pings a Discord channel with
color-coded, urgency-tagged embeds. Runs free on GitHub Actions cron.

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
- `config/companies.yaml` — the watch-list (slug + ats + tier). Grow it freely; bad slugs are skipped.

## How it works

`sources` fetch all boards concurrently -> `dedup` drops already-seen -> `filters`
free rules pre-filter -> `scorer` LLM-scores survivors -> `urgency` classifies ->
`notify` posts to Discord (or console in dry-run). See `docs/superpowers/`.
