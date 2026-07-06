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

RELEVANCE_MIN = 60   # strict gate: a discovered board must have a role scoring at least this

# Internship/new-grad list repos we harvest company ATS slugs from. These are DISCOVERY
# sources only (we then poll the boards directly). All URLs below were probed live; dead
# ones are skipped gracefully, so it's safe to keep a wide set. Mining new-grad lists too
# is intentional: the COMPANY is what we want to watch (it'll post co-op/intern roles), even
# if the new-grad role itself gets filtered out.
LIST_SOURCES = [
    # listings.json repos (scanned as raw text below, so JSON or markdown both work)
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2025-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/cvrve/New-Grad-2025/main/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/Ouckah/Summer2025-Internships/main/.github/scripts/listings.json",
    # README-based repos (Canadian + AI/ML focused); the text scan reads these fine too
    "https://raw.githubusercontent.com/speedyapply/2026-SWE-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/speedyapply/2026-AI-College-Jobs/main/README.md",
    "https://raw.githubusercontent.com/negarprh/Canadian-Tech-Internships-2026/main/README.md",
    # Canada-specific crowdsourced lists (verified live + carrying extractable ATS apply URLs)
    "https://raw.githubusercontent.com/hanzili/canada_sde_intern_position/main/README.md",
    "https://raw.githubusercontent.com/hanzili/canada_sde_junior_new_grad_position/main/README.md",
    "https://raw.githubusercontent.com/jenndryden/Canadian-Tech-Internships-and-New-Grad-2025/main/README.md",
    "https://raw.githubusercontent.com/isaiahiruoha/Canadian-Tech-And-Business-Internships-Summer-2025/main/README.md",
]
PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    # Workable: first path segment after apply.workable.com is the account slug (the
    # '/j/<code>' shortlink form captures 'j', which JUNK drops).
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9][a-z0-9-]+)", re.I)),
]
# Workday needs three coordinates (tenant.host.myworkdayjobs.com/<locale?>/<site>), not just
# a slug, so it gets its own parser. An optional locale segment like 'en-US/' is skipped.
WORKDAY = re.compile(
    r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[A-Za-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_-]+)", re.I)
# Oracle Recruiting Cloud needs a host code (e.g. 'cva.fa.us1') + a site number (e.g. 'CX_3');
# host is stored as the slug, site as wd_site. Its own parser, like Workday.
ORACLE = re.compile(
    r"([a-z0-9]+\.fa\.[a-z0-9]+)\.oraclecloud\.com/hcmUI/CandidateExperience/[a-z]+/sites/([A-Za-z0-9_]+)", re.I)
JUNK = {"embed", "job_app", "for", "www", "jobs", "careers", "o", "en", "en-us", "search", "j"}
# SmartRecruiters company IDs are case-sensitive (Square, Visa); others are lowercase.
_CASE_SENSITIVE = {"smartrecruiters"}


def _extract(url):
    """Single-URL -> (ats, slug) for the simple ATSs (used in tests). Workday is excluded
    here since it needs host + site; see _scan."""
    for ats, pat in PATTERNS:
        m = pat.search(url or "")
        if m:
            raw = m.group(1)
            slug = raw if ats in _CASE_SENSITIVE else raw.lower()
            if slug.lower() not in JUNK and len(slug) > 1:
                return ats, slug
    return None


def _scan(text: str, found: dict) -> None:
    """Scan raw text (a JSON listings file or a markdown README) for every ATS apply URL,
    adding {(ats, slug_lower): record} to `found`. Greenhouse/Lever/Ashby/SmartRecruiters
    need only a slug; Workday also captures host + site so its CXS endpoint can be built."""
    for ats, pat in PATTERNS:
        for m in pat.finditer(text):
            raw = m.group(1)
            slug = raw if ats in _CASE_SENSITIVE else raw.lower()
            if slug.lower() not in JUNK and len(slug) > 1:
                found.setdefault((ats, slug.lower()), {"slug": slug, "ats": ats})
    for m in WORKDAY.finditer(text):
        tenant, host, site = m.group(1).lower(), m.group(2).lower(), m.group(3)
        if tenant not in JUNK and len(tenant) > 1 and site and site.lower() not in JUNK:
            found.setdefault(("workday", tenant),
                             {"slug": tenant, "ats": "workday", "wd_host": host, "wd_site": site})
    for m in ORACLE.finditer(text):
        host, site = m.group(1).lower(), m.group(2)
        if len(host) > 1 and site and site.lower() not in JUNK:
            found.setdefault(("oracle", host),
                             {"slug": host, "ats": "oracle", "wd_site": site})


async def mine_candidates(client) -> dict:
    """(ats, slug_lower) -> record, harvested from the list/README repos. Scans the raw
    response TEXT (not just parsed JSON), so JSON listings and markdown READMEs both work
    and every supported ATS (incl. Workday's host/site) is picked up."""
    found = {}
    for src in LIST_SOURCES:
        try:
            r = await client.get(src)
            if r.status_code != 200:
                continue
            _scan(r.text, found)
        except Exception:
            continue
    return found


async def is_relevant(client, company: Company, profile) -> bool:
    """STRICT gate: True only if the board currently has a posting that passes the rules AND
    scores at least RELEVANCE_MIN on the free heuristic. Keeps out companies whose only
    'match' is a marginal rules-passing role. Used when discovering with --strict."""
    adapter = ADAPTERS.get(company.ats)
    if adapter is None:
        return False
    try:
        posts = await adapter.fetch(client, company)
    except Exception:
        return False
    return any(passes_rules(p, profile) and heuristic_score(p, profile).value >= RELEVANCE_MIN
               for p in posts)


def _title_ok(title: str, profile) -> bool:
    """A tech / early-career title: matches an include keyword and no exclude keyword.
    Location and freshness are deliberately ignored here (see board_qualifies)."""
    t = (title or "").lower()
    if any(x in t for x in profile.title_exclude):
        return False
    return (not profile.title_include) or any(x in t for x in profile.title_include)


async def board_qualifies(client, company: Company, profile) -> bool:
    """WIDE gate (the default for the weekly run): True if the board has ANY role with a
    tech/early-career title, regardless of that role's location, age, or heuristic score.
    We're only deciding whether to POLL this company from now on; a tech employer hiring
    today will post co-op/intern roles later, and the per-poll Sonnet score is the real
    relevance filter. This casts the widest sensible net (tech employers), while still
    skipping pure non-tech boards (a retailer with only store-manager roles) and dead ones."""
    adapter = ADAPTERS.get(company.ats)
    if adapter is None:
        return False
    try:
        posts = await adapter.fetch(client, company)
    except Exception:
        return False
    return any(_title_ok(p.title, profile) for p in posts)


async def discover(companies_path="config/companies.yaml", profile_path="config/profile.yaml",
                   *, max_add=40, max_probe=400, client=None, gate=None):
    gate = gate or is_relevant   # default strict; the weekly run passes board_qualifies (wide)
    profile = load_profile(profile_path)
    data = yaml.safe_load(Path(companies_path).read_text()) or {"companies": []}
    existing = data.get("companies", [])
    have = {c["slug"].lower() for c in existing}

    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
    try:
        mined = await mine_candidates(client)            # {(ats, slug_lower): record}
        cands = [rec for (ats, sl), rec in mined.items() if sl not in have]
        random.shuffle(cands)         # probe a different slice each week so coverage spreads
        cands = cands[:max_probe]

        sem = asyncio.Semaphore(16)   # be polite to the ATS APIs

        async def check(rec):
            async with sem:
                co = Company(slug=rec["slug"], ats=rec["ats"],
                             wd_host=rec.get("wd_host"), wd_site=rec.get("wd_site"))
                return rec if await gate(client, co, profile) else None

        relevant = [r for r in await asyncio.gather(*[check(r) for r in cands]) if r]
    finally:
        if owns:
            await client.aclose()

    add = relevant[:max_add]
    # Workday rows carry host+site, Oracle rows carry a site; the others are just slug,ats,tier.
    def _line(r):
        if r["ats"] == "workday":
            return f"{r['slug']},workday,target,{r['wd_host']},{r['wd_site']}"
        if r["ats"] == "oracle":
            return f"{r['slug']},oracle,target,{r['wd_site']}"
        return f"{r['slug']},{r['ats']},target"
    lines = [_line(r) for r in add]
    merged, added = merge(existing, lines)
    print(f"discover: {len(mined)} mined, probed {len(cands)}, "
          f"{len(relevant)} relevant, adding {added}")
    for r in add:
        print(f"  + {r['ats']:15} {r['slug']}")
    return merged, added, data


def main(argv=None):
    ap = argparse.ArgumentParser(prog="job_radar.discover")
    ap.add_argument("--companies", default="config/companies.yaml")
    ap.add_argument("--profile", default="config/profile.yaml")
    ap.add_argument("--max-add", type=int, default=80)
    ap.add_argument("--max-probe", type=int, default=800)
    ap.add_argument("--strict", action="store_true",
                    help="only add boards with a role scoring >=60 (default: wide gate, any "
                         "board with a tech/early-career-titled role)")
    ap.add_argument("--dry-run", action="store_true", help="report only; don't write the yaml")
    args = ap.parse_args(argv)

    gate = is_relevant if args.strict else board_qualifies
    merged, added, data = asyncio.run(discover(
        args.companies, args.profile, max_add=args.max_add, max_probe=args.max_probe, gate=gate))
    if added and not args.dry_run:
        data["companies"] = merged
        Path(args.companies).write_text(yaml.safe_dump(data, sort_keys=False))
        print(f"wrote {args.companies} (now {len(merged)} boards)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
