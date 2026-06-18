import asyncio
import os
from dataclasses import replace
from datetime import datetime, timezone

from .dedup import SeenStore
from .filters import passes_rules
from .models import Score, Urgency
from .notify import ConsoleNotifier, DiscordNotifier
from .scorer import (BedrockProvider, ClaudeProvider, FallbackProvider, GeminiProvider,
                     HeuristicProvider, heuristic_score)
from .sources import enrich_postings, fetch_all
from .urgency import classify

SCORE_CONCURRENCY = 6
PREVIEW_CAP = 80    # max survivors to LLM-score in --preview (use --company/--limit to narrow)
SHEET_MIN_FIT = 60  # the cron only mirrors genuinely-good matches to the Sheet (keep it curated)
# Max survivors to LLM-score in --backfill, bounding cost. The cron's run() is UNCAPPED
# (it only scores the small new-posting delta each poll); this cap bounds only the one-time
# deep sweep of the whole standing backlog. Scoring runs on Bedrock (paid, no tiny daily
# quota), so it's set wide to reach past the obvious top tier into the niche/startup roles
# the heuristic pre-rank buries. Raise further via the BACKFILL_CAP env var.
BACKFILL_CAP = int(os.environ.get("BACKFILL_CAP", "300"))


def _company_map(companies):
    return {(c.ats, c.slug): c for c in companies}


def _dedup_by_uid(postings):
    """Drop duplicate postings (same uid) within one poll, preserving order.
    A job can appear twice across paginated pages of one board."""
    seen_uids = set()
    out = []
    for p in postings:
        if p.uid in seen_uids:
            continue
        seen_uids.add(p.uid)
        out.append(p)
    return out


async def _score_all(provider, survivors, profile):
    """Score survivors concurrently (bounded), preserving input order. A failure on
    one posting yields a zero Score rather than aborting the whole run."""
    sem = asyncio.Semaphore(SCORE_CONCURRENCY)

    async def one(p):
        async with sem:
            try:
                return p, await provider.score(p, profile)
            except Exception as e:
                return p, Score(value=0, reason=f"score error: {e!r}"[:200], tags=[], ok=False)

    return await asyncio.gather(*[one(p) for p in survivors])


def build_provider(settings):
    """No key -> deterministic heuristic. With a key -> the LLM, but wrapped so that a
    scoring error (rate limit / outage) transparently falls back to the heuristic instead
    of dropping the posting. The radar stays useful even when the LLM is unavailable."""
    heuristic = HeuristicProvider()
    if settings.llm_provider == "bedrock":
        # Bedrock auths via the AWS credential chain (env/role), not an API key, so it's
        # available even without LLM_API_KEY. Pass the region EXPLICITLY: in CI there's no
        # ~/.aws/config, and boto3 doesn't reliably resolve the region from AWS_REGION alone
        # (NoRegionError), which would silently drop every score to the heuristic.
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        primary = BedrockProvider(settings.llm_model or "anthropic.claude-3-5-haiku-20241022-v1:0",
                                  region=region)
        return FallbackProvider(primary, heuristic)
    if not settings.llm_api_key:
        return heuristic
    if settings.llm_provider == "claude":
        primary = ClaudeProvider(settings.llm_api_key, settings.llm_model or "claude-haiku-4-5")
    else:
        primary = GeminiProvider(settings.llm_api_key, settings.llm_model or "gemini-2.5-flash")
    return FallbackProvider(primary, heuristic)


def build_notifier(settings):
    if settings.dry_run or not settings.webhook_url:
        return ConsoleNotifier()
    return DiscordNotifier(settings.webhook_url, settings.role_id)


def build_sheet_sink(settings):
    """Connect a SheetSink if a Sheet is configured AND its creds file exists; else
    None (local dev / Sheet not set up). A connection failure disables the Sheet
    rather than aborting the poll: job pings are the primary job, the Sheet is a mirror."""
    if not (settings.sheet_id and settings.creds_path and os.path.exists(settings.creds_path)):
        return None
    try:
        from . import sheet
        return sheet.SheetSink(sheet.connect(settings.creds_path, settings.sheet_id))
    except Exception as e:
        print(f"job-radar: Sheet disabled ({e!r})")
        return None


async def preview(config, *, provider=None, now=None):
    """Show what the bot would surface from the CURRENT backlog: rules-filter all
    postings, score (capped), and print ranked by fit. Read-only: ignores and never
    writes the seen-set. For tuning profile.yaml before going live."""
    now = now or datetime.now(timezone.utc)
    profile, companies, settings = config.profile, config.companies, config.settings
    provider = provider or build_provider(settings)
    cmap = _company_map(companies)

    postings, errors = await fetch_all(companies)
    survivors = [p for p in postings if passes_rules(p, profile, now)]
    to_score = survivors[:PREVIEW_CAP]
    to_score = await enrich_postings(to_score, cmap)
    scored = list(await _score_all(provider, to_score, profile))
    scored.sort(key=lambda ps: ps[1].value, reverse=True)

    for p, score in scored:
        level = classify(p, score, cmap.get((p.ats, p.company)), profile, now)
        tag = level.value.upper() if level else "drop"
        print(f"[{tag:6}] {score.value:3}/100  {p.title}  @ {p.company} ({p.location})  :: {score.reason}")

    stats = {"boards": len(companies), "postings": len(postings), "errors": len(errors),
             "survivors": len(survivors), "scored": len(scored),
             "truncated": max(0, len(survivors) - PREVIEW_CAP)}
    print("job-radar PREVIEW:", stats)
    return stats


async def backfill(config, *, provider=None, sheet_sink=None, now=None,
                   max_age_days=60, min_fit=60):
    """One-time inventory load: rules-filter the CURRENT backlog (reaching back up to
    max_age_days, wider than the cron's freshness window, to surface still-open older
    roles), score the freshest BACKFILL_CAP survivors, and write those scoring >= min_fit
    into the Sheet. min_fit is stricter than the cron's digest threshold so a bulk load
    stays focused on strong matches instead of every rules-survivor. Does NOT touch the
    seen-set and does NOT ping Discord. SheetSink dedups, so re-running is safe."""
    now = now or datetime.now(timezone.utc)
    profile, companies, settings = config.profile, config.companies, config.settings
    # Reach further back than the live cron: still-open roles posted weeks ago are exactly
    # what a backfill should catch (the cron only ever saw the last freshness_days window).
    profile = replace(profile, freshness_days=max(profile.freshness_days, max_age_days))
    provider = provider or build_provider(settings)
    if sheet_sink is None:
        sheet_sink = build_sheet_sink(settings)
    if sheet_sink is None:
        raise SystemExit("backfill: no Sheet configured "
                         "(set GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_PATH)")
    cmap = _company_map(companies)

    postings, errors = await fetch_all(companies)
    postings = _dedup_by_uid(postings)
    survivors = [p for p in postings if passes_rules(p, profile, now)]
    # Skip roles already in the Sheet BEFORE scoring, so a re-run only spends LLM calls on
    # genuinely-new postings (e.g. after widening the filter to US roles).
    survivors = [p for p in survivors if not sheet_sink.is_tracked(p.uid)]
    # Pre-rank by the FREE heuristic (rewards early-career + skill match, penalizes
    # seniority/domain-mismatch) and LLM-score the top BACKFILL_CAP. Across a big, loose
    # pool (e.g. US roles) this beats freshest-first, which surfaces marginal sales/support
    # roles that pass the title rules but aren't a fit.
    survivors.sort(key=lambda p: heuristic_score(p, profile).value, reverse=True)
    to_score = await enrich_postings(survivors[:BACKFILL_CAP], cmap)
    scored = await _score_all(provider, to_score, profile)

    for p, score in scored:
        # Inventory gate: a real score (not an error) at or above min_fit. Unlike the cron
        # we don't apply the dream-tier bypass here, since a weak/unscored dream role isn't
        # worth a row in a bulk load.
        if score.ok and score.value >= min_fit:
            sheet_sink.add(p, score)
    try:
        tracked = await asyncio.to_thread(sheet_sink.flush)  # one batched write
    except Exception as e:
        errors.append(("sheet", f"flush: {e!r}"))
        tracked = 0

    score_errors = sum(1 for _, s in scored if not s.ok)
    stats = {"boards": len(companies), "postings": len(postings), "errors": len(errors),
             "survivors": len(survivors), "scored": len(scored), "score_errors": score_errors,
             "truncated": max(0, len(survivors) - BACKFILL_CAP), "tracked": tracked}
    print("job-radar BACKFILL:", stats)
    if score_errors:
        print(f"NOTE: {score_errors} postings failed scoring (likely LLM rate/quota limit); "
              f"they were skipped, not written. Re-run --backfill once quota resets.")
    if errors:
        print("errors (first 10):", errors[:10])
    return stats


async def run(config, *, provider=None, notifier=None, sheet_sink=None, now=None, force_prime=False):
    now = now or datetime.now(timezone.utc)
    profile, companies, settings = config.profile, config.companies, config.settings
    provider = provider or build_provider(settings)
    notifier = notifier or build_notifier(settings)
    if sheet_sink is None:
        sheet_sink = build_sheet_sink(settings)
    cmap = _company_map(companies)

    seen = SeenStore(settings.seen_path).load()
    postings, errors = await fetch_all(companies)
    postings = _dedup_by_uid(postings)   # a job can repeat across paginated pages
    # For closed-role detection: every uid currently open, and the boards that fetched OK
    # this run (so a transient board error can't wrongly mark its roles Closed). Captured
    # here, before `errors` accrues later notify/sheet failures.
    open_uids = {p.uid for p in postings}
    fetch_failed = {slug for slug, _ in errors}
    new = [p for p in postings if seen.is_new(p)]

    # Cold start (or explicit --prime): with no memory yet, prime the seen-set silently
    # instead of flooding the channel with the entire current backlog. Real pings begin
    # on the next run. (If the persisted seen-set is ever lost, the next run re-primes
    # and skips one cycle of pings: an acceptable trade vs. a flood.)
    if force_prime or seen.is_empty():
        for p in postings:
            seen.mark(p, now)
        seen.save(now=now)
        stats = {"boards": len(companies), "postings": len(postings), "errors": len(errors),
                 "new": len(new), "primed": len(postings), "survivors": 0, "pinged": 0, "digest": 0}
        print("job-radar PRIMED (first run, no notifications):", stats)
        if errors:
            print("errors (first 10):", errors[:10])
        return stats

    # Per-company silent prime: a board newly added to the watch-list would otherwise have
    # its ENTIRE current backlog look "new" and flood the channel on the first poll. Absorb
    # those silently (they still get marked seen in the end-of-run sweep), so only their
    # genuinely-new future postings ping. Race-free: no separate re-prime step to mistime.
    known = seen.known_companies()
    fresh_co = {(c.ats, c.slug) for c in companies if (c.ats, c.slug) not in known}
    primed_new = 0
    if fresh_co:
        kept = [p for p in new if (p.ats, p.company) not in fresh_co]
        primed_new = len(new) - len(kept)
        new = kept

    survivors = [p for p in new if passes_rules(p, profile, now)]
    survivors = await enrich_postings(survivors, cmap)  # fill descriptions for the few that need it
    scored = await _score_all(provider, survivors, profile)

    digest = []
    pinged = 0
    pinged_keys = set()   # (company, title): ping a role once even if posted as several reqs
    for p, score in scored:
        company = cmap.get((p.ats, p.company))
        # Sheet gets every genuinely-good match (>= SHEET_MIN_FIT), INDEPENDENT of whether
        # it's ping-worthy, so it stays the full triage list even with a high ping bar. Skip
        # error scores (e.g. an LLM 429) so nothing lands as a bogus Fit 0 row.
        if sheet_sink is not None and score.ok and score.value >= SHEET_MIN_FIT:
            sheet_sink.add(p, score)
        level = classify(p, score, company, profile, now)
        if level is None:
            continue
        if level == Urgency.LOW:
            digest.append((p, score, company))
        else:
            key = (p.company, (p.title or "").strip().lower())
            if key in pinged_keys:   # same role as several reqs/locations -> ping once
                continue
            pinged_keys.add(key)
            try:
                await notifier.send_one(p, score, level, company, now)
                pinged += 1
            except Exception as e:  # a webhook hiccup must not abort the run
                errors.append((p.company, f"notify: {e!r}"))

    # One batched write (the Sheets API caps per-row writes at ~60/min). A Sheets hiccup
    # is recorded but can't abort the run.
    tracked = 0
    closed = 0
    if sheet_sink is not None:
        try:
            tracked = await asyncio.to_thread(sheet_sink.flush)
        except Exception as e:
            errors.append(("sheet", f"flush: {e!r}"))
        # Flag tracked roles that have vanished from a board we polled OK as Closed, so the
        # tracker reflects what's still applyable. Only boards that fetched cleanly this run.
        from . import sheet
        ok_boards = {c.slug for c in companies} - fetch_failed
        try:
            closed = await asyncio.to_thread(sheet.mark_closed, sheet_sink.ws, open_uids, ok_boards)
        except Exception as e:
            errors.append(("sheet", f"mark_closed: {e!r}"))
    try:
        await notifier.send_digest(digest, now)
    except Exception as e:
        errors.append(("digest", repr(e)))

    # Mark every fetched posting seen (refreshing its timestamp so long-lived listings
    # never age out and re-fire), then ALWAYS persist. A failing webhook can neither
    # abort the run nor leave the seen-set unsaved (which would re-send delivered items).
    for p in postings:
        seen.mark(p, now)
    seen.save(now=now)

    stats = {
        "boards": len(companies), "postings": len(postings), "errors": len(errors),
        "new": len(new), "primed": primed_new, "survivors": len(survivors), "pinged": pinged,
        "digest": len(digest), "tracked": tracked, "closed": closed,
        "score_errors": sum(1 for _, s in scored if not s.ok),
    }
    print("job-radar run:", stats)
    if errors:
        print("errors (first 10):", errors[:10])
    return stats
