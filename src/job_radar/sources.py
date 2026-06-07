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
