"""Auto-discovery: keep the watch-list growing itself, so nobody has to hand-add boards.

Mines candidate company slugs from the big internship-list repos (which are downstream
of the same ATS boards we poll, but a fine DISCOVERY source), then adds a company ONLY
if its board currently has at least one posting that passes the profile's rules. That
relevance gate is the whole trick: the list grows with companies that actually have roles
Taka would want, instead of bloating with thousands of irrelevant US-only boards.

Run weekly by .github/workflows/discover.yml (commits the result). Newly-added boards are
absorbed silently by the per-company prime on the next poll, so this never floods Discord.

Local: python -m job_radar.discover [--max-add N] [--max-probe N] [--dry-run]
"""
import argparse
import asyncio
import random
import re
from pathlib import Path

import httpx
import yaml

from .adapters.base import TIMEOUT
from .config import load_profile
from .filters import passes_rules
from .models import Company
from .scorer import heuristic_score
from .seed import merge
from .sources import ADAPTERS

RELEVANCE_MIN = 60   # a discovered board must have a role scoring at least this (heuristic)

LIST_SOURCES = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/cvrve/Summer2026-Internships/main/.github/scripts/listings.json",
]
PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
]
JUNK = {"embed", "job_app", "for", "www", "jobs", "careers", "o"}


def _extract(url):
    for ats, pat in PATTERNS:
        m = pat.search(url or "")
        if m:
            slug = m.group(1).lower()
            if slug not in JUNK and len(slug) > 1:
                return ats, slug
    return None


async def mine_candidates(client) -> dict:
    """slug -> ats, harvested from the list repos' apply URLs."""
    found = {}
    for src in LIST_SOURCES:
        try:
            r = await client.get(src)
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue
        for e in (data if isinstance(data, list) else data.get("listings", [])):
            hit = _extract(e.get("url") or e.get("apply_link") or "")
            if hit:
                found.setdefault(hit[1], hit[0])
    return found


async def is_relevant(client, company: Company, profile) -> bool:
    """True if the board currently has a posting that passes the rules AND scores at least
    RELEVANCE_MIN on the free heuristic. The heuristic gate keeps out companies whose only
    'match' is a marginal rules-passing role (e.g. a generic non-tech 'analyst'), so the
    watch-list grows with genuinely promising boards, not noise."""
    adapter = ADAPTERS.get(company.ats)
    if adapter is None:
        return False
    try:
        posts = await adapter.fetch(client, company)
    except Exception:
        return False
    return any(passes_rules(p, profile) and heuristic_score(p, profile).value >= RELEVANCE_MIN
               for p in posts)


async def discover(companies_path="config/companies.yaml", profile_path="config/profile.yaml",
                   *, max_add=40, max_probe=400, client=None):
    profile = load_profile(profile_path)
    data = yaml.safe_load(Path(companies_path).read_text()) or {"companies": []}
    existing = data.get("companies", [])
    have = {c["slug"].lower() for c in existing}

    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
    try:
        cands = {s: a for s, a in (await mine_candidates(client)).items() if s.lower() not in have}
        pool = list(cands.items())
        random.shuffle(pool)          # probe a different slice each week so coverage spreads
        pool = pool[:max_probe]

        sem = asyncio.Semaphore(16)   # be polite to the ATS APIs

        async def check(slug, ats):
            async with sem:
                ok = await is_relevant(client, Company(slug=slug, ats=ats), profile)
                return (slug, ats) if ok else None

        relevant = [r for r in await asyncio.gather(*[check(s, a) for s, a in pool]) if r]
    finally:
        if owns:
            await client.aclose()

    add = relevant[:max_add]
    lines = [f"{slug},{ats},target" for slug, ats in add]
    merged, added = merge(existing, lines)
    print(f"discover: {len(cands)} new candidates, probed {len(pool)}, "
          f"{len(relevant)} relevant, adding {added}")
    for slug, ats in add:
        print(f"  + {ats:11} {slug}")
    return merged, added, data


def main(argv=None):
    ap = argparse.ArgumentParser(prog="job_radar.discover")
    ap.add_argument("--companies", default="config/companies.yaml")
    ap.add_argument("--profile", default="config/profile.yaml")
    ap.add_argument("--max-add", type=int, default=40)
    ap.add_argument("--max-probe", type=int, default=400)
    ap.add_argument("--dry-run", action="store_true", help="report only; don't write the yaml")
    args = ap.parse_args(argv)

    merged, added, data = asyncio.run(discover(
        args.companies, args.profile, max_add=args.max_add, max_probe=args.max_probe))
    if added and not args.dry_run:
        data["companies"] = merged
        Path(args.companies).write_text(yaml.safe_dump(data, sort_keys=False))
        print(f"wrote {args.companies} (now {len(merged)} boards)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
