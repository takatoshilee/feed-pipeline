# Job Radar v2: Interactive Tracker Bot — Design Spec

**Date:** 2026-06-07
**Status:** Approved (brainstormed with Taka)
**Builds on:** the v1 job-radar engine (adapters/filters/scorer/sources reused as a library)

## Goal
Turn the one-way cron alerter into an application **tracker**: every relevant match lands
in a Google Sheet Taka can sort/filter/edit; Discord pings the high-fit ones with action
buttons; reminders nudge on deadlines and unapplied strong matches.

## Why these choices
- **Always-on Discord bot** (Taka's choice): real buttons + slash commands need a bot
  application + a running process (webhooks can't do interactive components).
- **Google Sheet as the single source of truth** (Taka wants a spreadsheet view): one row
  per tracked match; Taka edits cells directly OR clicks Discord buttons, both land here.
- Reuse the v1 engine wholesale; this is a new front-end + a tracker store, not a rewrite.

## Architecture
One process: `python -m job_radar.bot` (discord.py). Components:
- `bot.py` — discord.py client: a 15-min `tasks.loop` poller, a daily reminder loop,
  button views, slash commands, channel posting.
- `sheet.py` — Google Sheets I/O via `gspread` + a service account. Append match rows,
  read all rows, update a row's Status/Deadline/Applied-on by uid.
- `tracker.py` — pure logic over rows: which are due-soon, which are unapplied-and-strong,
  what `/top` returns. Unit-testable without network.
- Reuse: `sources.fetch_all`, `enrich_postings`, `filters.passes_rules`, `scorer`,
  `urgency.classify`, `config.load_profile`. The local seen-set (`dedup.SeenStore`, JSON
  file) still dedups all ~28k postings; only NEW survivors reach the Sheet.

## Data model (the Sheet)
One worksheet, header row, one row per tracked match keyed by `uid` (hidden/first column):
`uid | Company | Role | Link | Fit | Location | Posted | Status | Deadline | Priority | Notes | Applied on`
- **Status** dropdown: `New / Applied / Skip / Interviewing / Offer / Rejected` (default `New`).
- **Fit** = LLM score 0-100 (auto). Sheet is sorted by Fit desc by default for triage.
- **Priority / Deadline / Notes** = Taka-editable. Deadline auto-filled only when the
  posting states one; else blank for Taka to set.

## Data flow
**Poll loop (every 15 min):**
1. `fetch_all` → dedup via local seen-set → rule-filter → LLM-score survivors.
2. For each new survivor scoring >= ping threshold: append a `New` row to the Sheet
   (skip if its uid is already a row — Sheet is also a dedup backstop).
3. For HIGH-urgency / dream-company ones: also post a Discord embed with buttons
   **[Applied] [Not for me] [Set deadline]** (persistent custom_ids encoding the uid).
4. Mark all fetched postings seen (v1 semantics) and save.

**Interactions:**
- Button **Applied** → set that row's Status=`Applied`, Applied-on=today; edit the message to show it.
- Button **Not for me** → Status=`Skip`.
- Button **Set deadline** → opens a modal; the typed date writes to the Deadline cell.
- Slash commands: `/pending` (top unapplied `New` by Fit), `/due` (deadlines within 7 days),
  `/top` (best N unapplied right now), `/stats` (counts by Status).

**Reminder loop (daily):**
- **Due soon:** Deadline within 3 days and Status not in {Applied, Skip} → ping.
- **Unapplied nudge:** Status `New`, Fit high, first-seen > 3 days ago → "N strong roles unapplied."
- One-line pipeline summary (new / applied / due this week).

## Config / secrets (local `.env`, never committed)
`DISCORD_BOT_TOKEN`, `DISCORD_CHANNEL_ID`, `GOOGLE_SHEET_ID`, `GOOGLE_CREDENTIALS_PATH`,
`LLM_API_KEY` (reused). New deps: `discord.py`, `gspread`, `google-auth`. `.gitignore`
covers `.env` and `google-creds.json`.

## Hosting
Start on Taka's laptop (`python -m job_radar.bot`); move to a free always-on VM (Oracle
Cloud Always-Free) later. The v1 GitHub Actions cron is retired once the bot runs (the bot
has its own poll loop); the test workflow stays.

## Testing
`tracker.py` logic table-tested (due-soon, unapplied, top, stats) with sample rows.
`sheet.py` tested against a fake gspread client (no network). Bot wiring smoke-tested with
discord.py's testable pieces; button/command handlers kept thin over `tracker.py`.

## Build phasing
- **M1:** bot + sheet append/read + Applied/Skip buttons + `/pending` `/due` `/top` `/stats`
  + due-soon & unapplied reminders. The core loop, end to end.
- **M2:** Set-deadline modal + deadline auto-extract from posting text, follow-up reminders,
  richer statuses, move to cloud host.

## Non-goals (v2)
Auto-applying; scraping deadlines that postings don't state; a custom web UI (the Sheet is
the UI); multi-user.
