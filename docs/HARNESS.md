# harness

Read this first. It is everything a fresh session needs to carry on with no memory of earlier chats.

## Goal

job radar polls company career boards directly, every 15 minutes, and surfaces new co-op and internship postings before the aggregators list them. An LLM scores each new posting against a private profile, survivors go to a Google Sheet tracker, and high scorers ping Discord. The work now is keeping it healthy: boards that silently break should get noticed and repaired, and the run should stay well inside its 15 minute slot.

## Current state

Built and working:

- 11 ATS adapters under `src/job_radar/adapters/`, the fetch layer in `sources.py`, filters, dedup, LLM scoring, the Sheet tracker, Discord notify, weekly board discovery.
- Production runs on GitHub Actions (`.github/workflows/radar.yml`), triggered every 15 minutes by an external cron service. 994 of the last 1,000 runs passed (Sept 30 to Oct 10, 2026).
- 151 tests pass.
- The watch-list is 3,307 boards and lives in the private repo `takatoshilee/job-radar-config` (`companies.yaml`, `profile.yaml`). CI clones it at runtime. The copies under `config/` in a local checkout are untracked and can be stale.

Known broken:

- Each run logs 160 to 230 board errors that do not fail the run. Most are connect timeouts that change from run to run. The rest are boards that are permanently dead until someone repoints them.
- 33 boards are still dead after the Oct 10 repair pass (17 were fixed that day). They are listed under checklist item 1.
- `pipeline.py` prints only the first 10 errors, so a dead board is invisible unless someone reproduces the fetch by hand.

Not a bug, do not chase it: the six failed runs on Oct 5 2026 (19:15 to 21:00 UTC) were cancellations. GitHub was slow to hand out runners, runs backed up, and the concurrency group keeps only one run pending, so each backed-up run was dropped when the next trigger arrived. The radar step itself took its usual 6 minutes.

What the last chat was in the middle of: repairing dead boards. Two patterns found the fixes and are worth reusing. First, Workday tenants migrate to new host numbers (wd5 to wd115, wd504, wd502) while keeping the same site name, so probing the existing site across hosts wd1 to wd12, wd100 to wd120 and wd500 to wd510 finds them. Second, `discover._scan` run over a company's own careers page pulls out the ATS link it now uses. Guessing `<slug>.com` for the domain produced false matches (a page linking to some other company's board), so only accept a match when the new slug or tenant is clearly the same company.

## Referee

```bash
pip install -e ".[dev,sheet]"
pytest -q
```

All tests must pass. That is the same check CI runs in `.github/workflows/test.yml`. There is no linter or type checker configured; do not add one unless a checklist item says so.

For any change that touches fetching or an adapter, also prove it against the live boards, read-only:

```bash
python -m job_radar --dry-run --company <slug>
```

`--dry-run` prints instead of posting. Never run without it in a session.

## Rules

- Watch-list and profile changes go to `takatoshilee/job-radar-config`, never this repo. A change made only to a local `config/companies.yaml` never reaches CI.
- Never commit `config/companies.yaml`, `config/profile.yaml`, `google-creds.json`, `.env`, `.state/`, or anything under `.private-config/`. This repo is public.
- Never write a secret value into any file, commit message, or log line.
- Never run the radar, backfill, or reminders for real from a session. `--dry-run` only. A real run sends Discord pings and writes to the live Sheet.
- Do not change the trigger cadence, the `concurrency` block, or `timeout-minutes` in `radar.yml` without asking.
- A board is only repointed after it has been fetched through the real adapter and returned postings with no error. Do not delete a dead board; move it to the "unresolved" list in the Progress log so the company is not forgotten.
- Commit messages are plain prose describing the change. No AI attribution or co-author trailers, no session links.
- No em dashes in anything public (README, docs, commit messages).
- The README is first person and deliberately low profile. Keep its voice; no badges, no emoji.
- New adapters and fetch changes come with tests against recorded fixtures, like the existing ones in `tests/`.

## Checklist

- [ ] 1. Resolve the remaining dead boards. Greenhouse 404: hubspot, postman, marqeta, 10xgenomics, springhealth66, crossbeam, glossgenius, liveviewtechnologiesinc, rdccareers, midpointmarkets, buyersedgeplatformrecruiting, instabase, deepmind, ottoaviation, temporaltechnologies. Ashby 404: leapsome, lynk, two-dots, interaction, mach, klarity-ai, amigo, hiive. Lever 404: autoroboto. Workday 422 or 403: adobe, dell, nio, vca, justfab, milestone, montrose, telusinternational, amplify. Done when each one is either repointed in the private config and verified through the real adapter, or recorded as unresolved in the Progress log with what was tried.
- [ ] 2. Report errors in a form that can be acted on. Replace the "first 10" print in `pipeline.py` with a count by ATS and error kind (HTTP status or exception class), followed by the slugs for permanent failures (404, 403, 422). Done when a dry run prints the breakdown and a test covers the summary function.
- [ ] 3. Add a board health command, `python -m job_radar --health`, that fetches every board read-only, lists the permanent failures, and for Workday ones probes the other host numbers for the same site and prints the suggested fix. Done when it runs against the example config, has tests with a mocked client, and is described in two lines in the README.
- [ ] 4. Give connects a shorter timeout than reads in `adapters/base.py` (an `httpx.Timeout` with a connect value around 8 seconds), so an unreachable host costs less of the run. Done when tests pass and a full dry run finishes with no more errors than before the change.
- [ ] 5. Update the GitHub Actions that still target Node 20 (`actions/checkout`, `actions/setup-python`, `actions/cache`) to their current major versions in every workflow. Done when the test workflow passes on the branch and the deprecation warning is gone from its log.
- [ ] 6. Correct the board count in the README (it says about 2,900, the list is about 3,300). Done when the number matches the private config at the time of the change.

## Stop conditions

Stop and wait for me when:

- a requirement is unclear, or two readings would lead to different work
- anything needs a secret, a token, a payment, or an account you do not already have access to (including push access to the private config repo)
- a step would delete data, remove boards, rewrite history, or force push
- a change would alter what gets pinged to Discord or written to the Sheet
- a fix needs a design change bigger than the checklist item describes
- the Referee was passing and now fails for a reason you cannot explain

## How to work

Every session works on a new branch, never `main`.

Use a workflow with one agent per checklist item and an independent reviewer that checks each change against the Rules and runs the Referee commands.

After each item: run the Referee, tick the item off, add a dated line to the Progress log, commit, and push.

Only do checklist items. New ideas go at the bottom of the checklist instead of being done.

If an item hits a Stop condition or fails twice, log the blocker, skip it, and continue with the next item.

When finished, open a pull request summarizing what changed and what is blocked.

## Progress log

