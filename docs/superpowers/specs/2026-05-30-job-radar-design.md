# Job Radar — Design Spec

**Date:** 2026-05-30
**Status:** Draft for review
**Owner:** Takatoshi Lee

## 1. Goal & intent

Get **first dibs** on every job posting Taka could realistically apply to, **before** it spreads to LinkedIn/Indeed and gets flooded, while only ever being pinged about postings that **actually fit** him.

The strategy is a barbell: **maximize input coverage** (poll many company ATS boards directly, in near-real-time) and **aggressively filter output** (rules + LLM) so the Discord channel stays high-signal.

### Why this beats aggregators
Aggregators (LinkedIn, Indeed, most job bots) re-crawl on a schedule and are hours-to-days late. Lever/Ashby/Greenhouse each expose a **public, per-company JSON API**; a posting is live there the instant a recruiter publishes it. Polling those APIs directly puts us **upstream** of the aggregators. That latency gap is the entire edge.

## 2. Non-goals (v1)

- **Not** auto-applying. We notify; the human applies.
- **Not** scraping LinkedIn/Indeed/Glassdoor (we want to be upstream of them, not downstream).
- **Not** "all of Workday." Workday is per-tenant and partially bot-protected; v1 covers a **curated set of high-value Workday tenants** only.
- **Not** an interactive Discord bot in v1 (one-way webhook embeds only; slash commands are roadmap).
- **Not** a web UI. Config is two YAML files in the repo.

## 3. Success criteria

- New, relevant postings from watched boards reach Discord within **one cron cycle** (~15 min) of going live.
- The channel is **high-signal**: most pings are genuinely worth a click. Few false-positives.
- Adding a new company = one line in `companies.yaml`. Adding a new ATS = one new adapter file, no other changes.
- Runs **free** on GitHub Actions with no babysitting (modulo the documented cron caveats).
- LLM spend stays negligible because rules pre-filter before any tokens are spent.

## 4. Architecture

### 4.1 Pipeline (one run, every ~15 min)

```
companies.yaml ─▶ ATS adapters ─▶ raw postings ─▶ dedup (drop already-seen)
  (watch-list)   (gh/lever/                              │
                  ashby/wd)                    new postings only
                                                          ▼
                                        rules pre-filter (free, instant)
                                          driven by profile.yaml
                                                          │
                                                  survivors (handful)
                                                          ▼
                                        LLM scorer → {score 0-100, reason, tags}
                                                          │
                                                  score ≥ threshold
                                                          ▼
                                  urgency tagger (freshness + score + dream-co)
                                                          │
                                            🔴 / 🟡 / 🟢
                                                          ▼
                          Discord webhook embeds  +  LOW → batched digest
                                                          │
                                            mark postings seen (state cache)
```

### 4.2 Modules

| Module | Responsibility | Interface |
|---|---|---|
| `adapters/base.py` | `Posting` dataclass + `Adapter` protocol | `fetch(company) -> list[Posting]` |
| `adapters/greenhouse.py` | Greenhouse board API → `Posting[]` | implements `Adapter` |
| `adapters/lever.py` | Lever postings API → `Posting[]` | implements `Adapter` |
| `adapters/ashby.py` | Ashby posting API → `Posting[]` | implements `Adapter` |
| `adapters/workday.py` | Workday CXS API (curated tenants) → `Posting[]` | implements `Adapter` |
| `sources.py` | Load `companies.yaml` watch-list | `load_companies() -> list[Company]` |
| `dedup.py` | Seen-set load/save/check | `is_new(posting)`, `mark(posting)` |
| `filters.py` | Rules pre-filter from `profile.yaml` | `passes_rules(posting) -> bool` |
| `scorer.py` | LLM relevance scoring | `score(posting) -> Score` |
| `urgency.py` | Compute 🔴/🟡/🟢 | `urgency(posting, score) -> Level` |
| `notify.py` | Build + POST Discord embeds, digest | `send(posting, score, level)` |
| `config.py` | Load profile + secrets/env | typed config objects |
| `main.py` | Wire the pipeline; cron entrypoint | `python -m job_radar` |

Each module is independently unit-testable. Adapters are the only network-touching pieces and are isolated behind one interface so the rest of the pipeline is pure and deterministic.

### 4.3 Core data model

```python
@dataclass(frozen=True)
class Posting:
    uid: str            # stable global id: f"{ats}:{company}:{native_id}"
    ats: str            # "greenhouse" | "lever" | "ashby" | "workday"
    company: str        # slug / display name
    title: str
    location: str       # normalized free text
    url: str            # direct apply/posting link
    posted_at: datetime | None   # best-effort; None if ATS doesn't expose it
    description: str     # plain-text, truncated for LLM
    raw: dict            # original payload, for debugging
```

`uid` is the dedup key and is stable across runs.

## 5. ATS adapters (the coverage engine)

All adapters normalize to `Posting`. Endpoints are public JSON, no auth.

- **Greenhouse** — `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`. Returns all jobs with HTML `content`, `location.name`, `absolute_url`, `updated_at`. Board token = company slug.
- **Lever** — `GET https://api.lever.co/v0/postings/{slug}?mode=json`. Returns `text` (title), `categories.location/commitment/team`, `hostedUrl`, `createdAt` (epoch ms → real posting time, best freshness signal), `descriptionPlain`. Caveat: Lever is migrating some customers; the v0 API still serves the large existing base. Adapter tolerates a company having no/empty board.
- **Ashby** — `GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`. Returns `title`, `location`, `employmentType`, `jobUrl`, `publishedAt`, `descriptionPlain`, `isListed`. Skip `isListed: false`.
- **Workday** (curated tenants, best-effort) — `POST https://{tenant}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` with body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}`, paginated by `offset` until `total`. Returns `jobPostings[]` with `title`, `externalPath`, `locationsText`, `postedOn` (**relative** text like "Posted Today"/"Posted 3 Days Ago" → approximate `posted_at`). Per-tenant host (`wd1/wd3/wd5/...`) and `site` are stored per-company in `companies.yaml`. Description needs a second GET; v1 may score on title+location+team only for Workday. May hit bot protection; failures degrade gracefully (logged, run continues).

**Resilience:** adapters run concurrently with a bounded semaphore (default 30), per-request timeout + retry-with-backoff on 429/5xx, and **a single board failing never aborts the run** — it's logged and skipped. Politeness: jittered concurrency, respect 429 `Retry-After`.

## 6. Company sourcing (going big)

`companies.yaml` is the watch-list; each entry: `{ slug, ats, tier, [workday: {host, site}] }`. `tier: dream` flags auto-HIGH companies.

Seeding strategy (breadth-first, per intent):
1. **Manual targets** — Taka's known list (Wealthsimple, Notion, Veeva, etc.), tier `dream`/`target`.
2. **Public slug dumps** — community-maintained lists of Greenhouse/Lever/Ashby company slugs (thousands), imported via a one-off `scripts/seed.py`.
3. **Discovery** — `site:boards.greenhouse.io`, `site:jobs.lever.co`, `site:jobs.ashbyhq.com` search harvesting to grow the list over time.
4. **Workday** — hand-picked tenants (banks, Nvidia, big enterprises) with their `host`/`site` resolved once.

The list is data, refreshable independently of code. Dead/invalid slugs are pruned automatically when an adapter 404s repeatedly.

## 7. Filtering (rules → LLM)

### 7.1 Rules pre-filter (free, deterministic) — `profile.yaml`-driven
Cuts thousands of raw postings to a handful before any tokens are spent:
- **Title include** — matches role keywords (intern, co-op, new grad, software, ml, ai, data, backend, ...).
- **Title/desc exclude** — senior, staff, principal, lead, manager, director, II/III, "5+ years", security clearance, etc.
- **Location** — allow Toronto / Canada / Remote-Canada / Remote-NA / specified; block hard-onsite-elsewhere.
- **Freshness** — only `posted_at` within last N days (default 14) when the ATS exposes it.

### 7.2 LLM scorer (only on survivors) — `scorer.py`
For each survivor, send Taka's profile summary + posting (title, company, location, truncated description) to a cheap model (Gemini Flash default; Claude Haiku pluggable) requesting **structured output**:
```json
{ "score": 0-100, "reason": "one line", "tags": ["ai-ml","intern","canada"] }
```
- Provider behind a `LLMProvider` interface; model + key from env.
- **Cost control:** rules ensure only a few calls/run; batch multiple postings per request when >1 survivor; cache scores by `uid`.
- **Thresholds:** `score ≥ 65` → ping; `50-64` → LOW digest; `< 50` → drop. **Exception:** a `tier: dream` company that passed the rules filter always pings (overrides the score threshold), so we never miss a dream-company role on a soft score.

### 7.3 Why both
Rules are cheap/blunt and keep tokens near zero; the LLM judges nuance ("is this *actually* new-grad / AI-ML-adjacent / a fit for a 2nd-year co-op?") that regex can't. Profile drives both, single source of truth.

## 8. Urgency model — `urgency.py`

| Level | Trigger | Action |
|---|---|---|
| 🔴 HIGH | (`posted_at` < ~2h **and** `score ≥ 80`) **or** `tier == dream` | individual embed + **role-ping** |
| 🟡 MEDIUM | `score ≥ 65` and not HIGH | individual embed, no ping |
| 🟢 LOW | `50 ≤ score < 65` | batched into periodic **digest**, no ping |

Freshness is the primary urgency signal (the "beat the market" edge), boosted by score and dream-company. We do **not** rely on closing deadlines (most ATSs don't expose them reliably).

## 9. Notifications — `notify.py`

- **Transport:** Discord **webhook** (no bot hosting, fits cron). URL from secret.
- **Embed per posting:** title (linked to apply URL), company, location, urgency color (red/amber/green), score + one-line LLM reason, tags, relative age, ATS source. Role-ping prepended for HIGH.
- **Digest:** LOW matches collected and posted as a single rolled-up embed on a slower cadence (e.g., once/day) to avoid spam.
- **Dry-run mode:** `--dry-run` prints embeds to console instead of POSTing (used in tests/dev).

## 10. Hosting, state & secrets

### 10.1 Runtime (v1, free)
- **GitHub Actions cron**, `schedule: */15 * * * *`. Entry: `python -m job_radar`.
- **Honest caveats:** GH scheduled runs are best-effort and can be delayed under load; a repo with **no commits for 60 days auto-disables** schedules. Mitigations: a tiny weekly keepalive workflow; and these vanish when we flip the trigger to an always-on host later (the pipeline code doesn't change).

### 10.2 State / dedup
- Seen-set = set of `uid`s. Persisted via **GitHub Actions cache** (`actions/cache` restore at start, save at end) so there's **no commit spam**.
- Stored compactly (JSON or SQLite); pruned to a rolling window (default 60 days) to bound size.
- **Pluggable** `Store` interface → swap to **Supabase** (Taka knows it) when scaling to thousands of boards or moving to an always-on host.

### 10.3 Config & secrets
- `profile.yaml`, `companies.yaml` committed to repo (no secrets).
- Secrets as **GitHub Action secrets**: `DISCORD_WEBHOOK_URL`, `LLM_API_KEY`, optional `DISCORD_ROLE_ID`.

## 11. Concurrency, politeness, errors

- Async (`httpx`) with bounded concurrency (default 30) + jitter.
- Timeouts + exponential backoff on 429/5xx; honor `Retry-After`.
- **Partial failure tolerated:** any single board/adapter error is logged and skipped; the run still delivers everything else.
- Structured logging (counts: boards polled, postings seen, new, passed-rules, scored, pinged) for visibility and tuning.

## 12. Testing

- **Adapter unit tests** against saved sample JSON fixtures (no live network in CI).
- **Filter rules** table-tested (include/exclude/location/freshness cases).
- **Urgency** table-tested across the threshold matrix.
- **Notifier** tested in `--dry-run` (asserts embed structure, no network).
- **Dedup** tested (new vs seen).
- A single live smoke-test script (`scripts/smoke.py`, run manually, not in CI) hits one real board per ATS.

## 13. Scope & build milestones

**v1 (this spec) — one coherent project, built in two milestones so a working pipeline ships fast:**
- **M1 (working end-to-end):** Greenhouse + Lever + Ashby adapters, sources, dedup, rules filter, LLM scorer, urgency, Discord webhook, GH Actions cron + cache state, dry-run, tests. → Real pings flowing.
- **M2 (coverage + breadth):** Workday adapter (curated tenants), big-seed import script, LOW digest, weekly keepalive, slug auto-prune.

**Roadmap (not now):** thousands-scale Supabase state, always-on host for sub-5-min latency, interactive Discord bot (`/applied`, `/mute`, `/filters`), per-company posting-history analytics, email/SMS fallback channel.

## 14. Assumptions & open questions

- **Assumed:** project lives in a new private repo `job-radar` (sibling of the resume repo); Python 3.12; Gemini Flash as default model; Discord server/channel + webhook already (or easily) created by Taka.
- **Open:** exact starting seed size for M2 (hundreds vs low-thousands) — tune after M1 shows real signal/noise; whether to also notify a 🟡 MEDIUM ping vs digest after observing volume; final list of Workday tenants worth the effort.
