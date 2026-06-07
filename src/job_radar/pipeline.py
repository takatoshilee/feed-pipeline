import asyncio
from datetime import datetime, timezone

from .dedup import SeenStore
from .filters import passes_rules
from .models import Urgency
from .notify import ConsoleNotifier, DiscordNotifier
from .scorer import ClaudeProvider, FakeProvider, GeminiProvider
from .sources import enrich_postings, fetch_all
from .urgency import classify

SCORE_CONCURRENCY = 6


def _company_map(companies):
    return {(c.ats, c.slug): c for c in companies}


async def _score_all(provider, survivors, profile):
    """Score survivors concurrently (bounded), preserving input order."""
    sem = asyncio.Semaphore(SCORE_CONCURRENCY)

    async def one(p):
        async with sem:
            return p, await provider.score(p, profile)

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


async def run(config, *, provider=None, notifier=None, now=None, force_prime=False):
    now = now or datetime.now(timezone.utc)
    profile, companies, settings = config.profile, config.companies, config.settings
    provider = provider or build_provider(settings)
    notifier = notifier or build_notifier(settings)
    cmap = _company_map(companies)

    seen = SeenStore(settings.seen_path).load()
    postings, errors = await fetch_all(companies)
    new = [p for p in postings if seen.is_new(p)]

    # Cold start (or explicit --prime): with no memory yet, prime the seen-set silently
    # instead of flooding the channel with the entire current backlog. Real pings begin
    # on the next run. (If the persisted seen-set is ever lost, the next run re-primes
    # and skips one cycle of pings: an acceptable trade vs. a flood.)
    if force_prime or seen.is_empty():
        for p in new:
            seen.mark(p, now)
        seen.save(now=now)
        stats = {"boards": len(companies), "postings": len(postings), "errors": len(errors),
                 "new": len(new), "primed": len(new), "survivors": 0, "pinged": 0, "digest": 0}
        print("job-radar PRIMED (first run, no notifications):", stats)
        if errors:
            print("errors (first 10):", errors[:10])
        return stats

    survivors = [p for p in new if passes_rules(p, profile, now)]
    survivors = await enrich_postings(survivors, cmap)  # fill descriptions for the few that need it
    scored = await _score_all(provider, survivors, profile)

    digest = []
    pinged = 0
    for p, score in scored:
        company = cmap.get((p.ats, p.company))
        level = classify(p, score, company, profile, now)
        if level is None:
            continue
        if level == Urgency.LOW:
            digest.append((p, score, company))
        else:
            await notifier.send_one(p, score, level, company, now)
            pinged += 1

    # Mark every posting seen this run so nothing re-fires next run. Survivors are
    # scored exactly once; rule-failures are recorded too so they aren't re-handled.
    # (To force a full re-scan after broadening the profile, clear the seen-set.)
    for p in new:
        seen.mark(p, now)

    await notifier.send_digest(digest, now)
    seen.save(now=now)

    stats = {
        "boards": len(companies), "postings": len(postings), "errors": len(errors),
        "new": len(new), "primed": 0, "survivors": len(survivors), "pinged": pinged, "digest": len(digest),
    }
    print("job-radar run:", stats)
    if errors:
        print("errors (first 10):", errors[:10])
    return stats
