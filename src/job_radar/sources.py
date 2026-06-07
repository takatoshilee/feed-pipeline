import asyncio

import httpx

from .adapters import ashby, greenhouse, lever, smartrecruiters, workday
from .adapters.base import TIMEOUT
from .models import Company, Posting

ADAPTERS = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "workday": workday,
    "smartrecruiters": smartrecruiters,
}


async def _fetch_one(client, sem, company, errors):
    adapter = ADAPTERS.get(company.ats)
    if adapter is None:
        errors.append((company.slug, f"no adapter for ats={company.ats}"))
        return []
    async with sem:
        try:
            return await adapter.fetch(client, company)
        except Exception as e:  # one board failing never aborts the run
            errors.append((company.slug, repr(e)))
            return []


async def fetch_all(companies, *, concurrency=30, client=None):
    sem = asyncio.Semaphore(concurrency)
    errors: list[tuple[str, str]] = []
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        results = await asyncio.gather(
            *[_fetch_one(client, sem, c, errors) for c in companies]
        )
    finally:
        if owns:
            await client.aclose()
    postings = [p for sub in results for p in sub]
    return postings, errors


# Adapters that expose enrich() to fetch a full description via a second call.
ENRICHERS = {"workday", "smartrecruiters"}


async def enrich_postings(postings, cmap, *, concurrency=10, client=None):
    """Fill in descriptions for the given postings (typically the survivors) whose
    adapter needs a second call. Returns a new list in the same order; failures keep
    the original posting. No-op for adapters that already include descriptions."""
    targets = [p for p in postings if p.ats in ENRICHERS and not p.description]
    if not targets:
        return list(postings)

    sem = asyncio.Semaphore(concurrency)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def one(p):
        company = cmap.get((p.ats, p.company))
        if company is None:
            return p
        async with sem:
            try:
                return await ADAPTERS[p.ats].enrich(client, p, company)
            except Exception:
                return p

    try:
        enriched = await asyncio.gather(*[one(p) for p in targets])
    finally:
        if owns:
            await client.aclose()

    by_uid = {orig.uid: new for orig, new in zip(targets, enriched)}
    return [by_uid.get(p.uid, p) for p in postings]
