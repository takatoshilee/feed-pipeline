# job radar

Watches company career pages directly and tells me when something I can actually apply to goes up.

Aggregators lag the source by hours or days, and they quietly miss the postings that never syndicate anywhere. This polls the ATS APIs companies publish themselves, so a role lands in my tracker about as fast as it lands on the company's own careers page. I built it in June 2026 for my own co-op search and it has been running since.

## what it does

Every 15 minutes:

1. pulls open postings from ~2,900 company boards across 11 ATS adapters
2. drops anything already seen, closed, or duplicated across sources
3. has an LLM read each new posting, score the fit against my profile, and extract the term dates, since a Summer 2026 role is useless to me and gets gated out
4. writes what survives to a Google Sheet and pings Discord for the high scorers

The sheet is the actual point. One row per role, colour-coded by state, applied-date stamped when I tick the box. I stopped keeping a separate tracker.

## adapters

Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Oracle, Google Careers, Amazon Jobs, and the Simplify community feeds.

Each one is a small module under `src/job_radar/adapters/` that turns a board into `Posting` objects, so adding another is usually an afternoon of reading someone's JSON. A few companies expose nothing usable (Microsoft's careers site is browser-locked), and those I still check by hand.

## the config is private

The watch-list and the scoring profile live in a separate private repo and get pulled at runtime in CI. They spell out which companies I am targeting and what I am eligible for, which I would rather not publish. `config/companies.example.yaml` and `config/profile.example.yaml` show the shape.

So this repo is the machinery without the targets. Bring your own list.

## running it

```bash
pip install -e ".[dev,sheet]"          # python 3.12+
cp config/companies.example.yaml config/companies.yaml
cp config/profile.example.yaml config/profile.yaml
python -m job_radar --dry-run          # prints instead of posting
```

Worth knowing:

- `--prime` marks everything currently open as seen without notifying. Run it once on a fresh checkout or the first real run will fire hundreds of pings.
- `--preview` ranks the current backlog, read-only.
- `--company <slug>` and `--limit N` when you are poking at one board.

Configuration is environment variables: `DISCORD_WEBHOOK_URL`, `GOOGLE_SHEET_ID` and `GOOGLE_CREDENTIALS_PATH`, and `LLM_PROVIDER` / `LLM_MODEL` / `LLM_API_KEY` for the scorer. Bedrock works too, set `AWS_REGION` for it.

## scheduling

GitHub Actions on `workflow_dispatch`, triggered every 15 minutes by an external cron service. GitHub's own `schedule:` is throttled and drifts badly at that cadence, so it sits in the workflow as a once-daily backup and nothing more. Runs are serialized with a concurrency group, because two overlapping runs will double-notify.

`SCORE_CAP` bounds how many postings reach the LLM in one run. Anything over the cap carries into the next run rather than being dropped, which matters during the fall wave when a few hundred roles can appear overnight.

## tests

```bash
pytest
```

151 of them. Mostly adapter parsing against recorded fixtures, plus the dedup and term-gating logic. Vendors change their JSON shapes without warning, so the fixtures are the early warning system for a silently broken adapter.
