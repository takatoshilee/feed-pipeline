import asyncio
import os
from datetime import datetime, timezone

from .dedup import SeenStore
from .filters import passes_rules
from .models import Score, Urgency
from .notify import ConsoleNotifier, DiscordNotifier
from .scorer import ClaudeProvider, FakeProvider, GeminiProvider
from .sources import enrich_postings, fetch_all
from .urgency import classify

SCORE_CONCURRENCY = 6
PREVIEW_CAP = 80    # max survivors to LLM-score in --preview (use --company/--limit to narrow)
# Max survivors to score in --backfill (freshest first), bounding LLM cost. Default
# stays under the Gemini free tier's ~200 requests/day (shared with the cron); raise
# via the BACKFILL_CAP env var if you have a paid key.
BACKFILL_CAP = int(os.environ.get("BACKFILL_CAP", "120"))


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
    if not settings.llm_api_key:
        return FakeProvider(value=70, reason="no LLM key; placeholder score")
    if settings.llm_provider == "claude":
        return ClaudeProvider(settings.llm_api_key, settings.llm_model or "claude-haiku-4-5")
    return GeminiProvider(settings.llm_api_key, settings.llm_model or "gemini-2.0-flash")


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


async def backfill(config, *, provider=None, sheet_sink=None, now=None):
    """One-time inventory load: rules-filter the CURRENT backlog, score the freshest
    BACKFILL_CAP survivors, and write those worth tracking into the Sheet. Does NOT
    touch the seen-set and does NOT ping Discord, so it can neither flood the channel
    nor change what the live poll considers already-seen. SheetSink dedups, so running
    it more than once is safe."""
    now = now or datetime.now(timezone.utc)
    profile, companies, settings = config.profile, config.companies, config.settings
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
    # Freshest first, so a capped backfill captures the most relevant current openings.
    survivors.sort(key=lambda p: p.posted_at or datetime.min.replace(tzinfo=timezone.utc),
                   reverse=True)
    to_score = await enrich_postings(survivors[:BACKFILL_CAP], cmap)
    scored = await _score_all(provider, to_score, profile)

    tracked = 0
    for p, score in scored:
        if not score.ok:  # don't write bogus Fit 0 rows when scoring errored (e.g. 429)
            continue
        level = classify(p, score, cmap.get((p.ats, p.company)), profile, now)
        if level is None:
            continue
        try:
            if await asyncio.to_thread(sheet_sink.add, p, score):
                tracked += 1
        except Exception as e:
            errors.append((p.company, f"sheet: {e!r}"))

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

    survivors = [p for p in new if passes_rules(p, profile, now)]
    survivors = await enrich_postings(survivors, cmap)  # fill descriptions for the few that need it
    scored = await _score_all(provider, survivors, profile)

    digest = []
    pinged = tracked = 0
    for p, score in scored:
        company = cmap.get((p.ats, p.company))
        level = classify(p, score, company, profile, now)
        if level is None:
            continue
        # Mirror real-scored matches (incl. digest-level) into the Sheet so it's the
        # full inventory to triage; Discord stays a heads-up for the urgent ones. Skip
        # error scores (e.g. an LLM 429) so a dream-tier post never lands as a bogus
        # Fit 0 row. A Sheet hiccup is recorded but can't abort the run.
        if sheet_sink is not None and score.ok:
            try:
                if await asyncio.to_thread(sheet_sink.add, p, score):
                    tracked += 1
            except Exception as e:
                errors.append((p.company, f"sheet: {e!r}"))
        if level == Urgency.LOW:
            digest.append((p, score, company))
        else:
            try:
                await notifier.send_one(p, score, level, company, now)
                pinged += 1
            except Exception as e:  # a webhook hiccup must not abort the run
                errors.append((p.company, f"notify: {e!r}"))
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
        "new": len(new), "primed": 0, "survivors": len(survivors), "pinged": pinged,
        "digest": len(digest), "tracked": tracked,
        "score_errors": sum(1 for _, s in scored if not s.ok),
    }
    print("job-radar run:", stats)
    if errors:
        print("errors (first 10):", errors[:10])
    return stats
