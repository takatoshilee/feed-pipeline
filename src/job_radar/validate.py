"""Check every company board once; report (and optionally prune) dead entries.

Run: python -m job_radar.validate [--prune] [--companies config/companies.yaml]

Bulk-imported slug lists always contain some dead/renamed slugs. This hits each
board once and labels it ok / empty / dead. `--prune` rewrites the YAML with the
dead ones removed. "empty" (a clean response with no postings) is kept: a board
can legitimately have nothing open right now.
"""
import argparse
import asyncio
from pathlib import Path

import httpx
import yaml

from .adapters.base import TIMEOUT
from .config import load_companies
from .sources import ADAPTERS


async def check(client, company):
    adapter = ADAPTERS.get(company.ats)
    if adapter is None:
        return (company, "no-adapter", 0)
    try:
        posts = await adapter.fetch(client, company)
    except Exception as e:
        return (company, f"error:{type(e).__name__}", 0)
    return (company, "ok" if posts else "empty", len(posts))


async def run_validate(companies, *, concurrency=20, client=None):
    sem = asyncio.Semaphore(concurrency)
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def one(c):
        async with sem:
            return await check(client, c)

    try:
        return await asyncio.gather(*[one(c) for c in companies])
    finally:
        if owns:
            await client.aclose()


def is_dead(status: str) -> bool:
    return status.startswith("error") or status == "no-adapter"


def prune(companies, results):
    dead = {(c.ats, c.slug) for (c, status, _n) in results if is_dead(status)}
    return [c for c in companies if (c.ats, c.slug) not in dead]


def _to_dict(c):
    d = {"slug": c.slug, "ats": c.ats, "tier": c.tier}
    if c.wd_host:
        d["wd_host"] = c.wd_host
    if c.wd_site:
        d["wd_site"] = c.wd_site
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(prog="job_radar.validate",
                                 description="Check every board; report dead/empty ones.")
    ap.add_argument("--companies", default="config/companies.yaml")
    ap.add_argument("--prune", action="store_true", help="remove erroring entries from the yaml")
    args = ap.parse_args(argv)

    companies = load_companies(args.companies)
    results = asyncio.run(run_validate(companies))

    for c, status, n in sorted(results, key=lambda r: r[1]):
        print(f"  {status:18} {c.ats:16} {c.slug:18} {n}")

    ok = sum(1 for _, s, _ in results if s == "ok")
    empty = sum(1 for _, s, _ in results if s == "empty")
    dead = [c for c, s, _ in results if is_dead(s)]
    print(f"\n{ok} ok, {empty} empty, {len(dead)} dead (of {len(results)})")

    if args.prune and dead:
        kept = prune(companies, results)
        Path(args.companies).write_text(
            yaml.safe_dump({"companies": [_to_dict(c) for c in kept]}, sort_keys=False))
        print(f"pruned {len(dead)} dead entries; {len(kept)} remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
