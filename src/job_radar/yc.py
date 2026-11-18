"""Y Combinator startups: find the job boards of YC companies that are hiring, add them to
the watch-list, and tag them `yc: true` so the Top 30 ranks their roles up.

Company list: the yc-oss mirror of YC's public company directory (hiring.json, rebuilt
daily from ycombinator.com/companies). For each company we guess board slugs from its YC
slug, its name and its website, and probe the ATS APIs the radar already polls (Ashby,
Greenhouse, Lever). A guess only counts when the board's postings mention the company by
name or domain, so a common-word slug ("loop", "hex") can't attach someone else's board.

Why not poll ycombinator.com/companies/<slug>/jobs directly: YC answers HTTP 429 after
about 60 page loads (tested Nov 18 2026), so 1,400 company pages every 15 minutes is not
an option. Companies that only post on Work at a Startup stay uncovered here.

Existing watch-list entries that match a YC company just get `yc: true` added.
New boards are absorbed silently by the per-company prime on the next poll.

Local: python -m job_radar.yc [--dry-run] [--max-companies N]
"""
import argparse
import asyncio
import re
from pathlib import Path

import httpx
import yaml

from .adapters.base import TIMEOUT, USER_AGENT
from .topn import norm

YC_LIST = "https://yc-oss.github.io/api/companies/hiring.json"

PROBES = {
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
}
AGGREGATORS = {"simplify", "canadalist"}


def _domain(website: str) -> str:
    """'https://www.pebl.com/careers' -> 'pebl.com'."""
    d = re.sub(r"^https?://", "", (website or "").strip().lower())
    d = d.split("/")[0]
    return d[4:] if d.startswith("www.") else d


def candidates(company: dict) -> list[str]:
    """Board slugs worth trying for one YC company, most likely first, no duplicates."""
    name = (company.get("name") or "").strip().lower()
    stem = _domain(company.get("website", "")).split(".")[0]
    raw = [company.get("slug") or "", norm(name), re.sub(r"[^a-z0-9]+", "-", name).strip("-"),
           stem, norm(stem)]
    out = []
    for s in raw:
        s = s.strip().lower()
        if len(s) >= 2 and s not in out:
            out.append(s)
    return out


def _jobs(ats: str, data) -> list:
    if ats == "lever":
        return data if isinstance(data, list) else []
    return (data or {}).get("jobs") or []


def mentions(company: dict, text: str) -> bool:
    """Does the board's raw payload name this company (whole word) or its domain?"""
    t = text.lower()
    dom = _domain(company.get("website", ""))
    if dom and dom in t:
        return True
    name = (company.get("name") or "").strip().lower()
    return bool(name) and re.search(r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])", t) is not None


async def probe(client, company: dict, sem) -> dict | None:
    """The best live board for one company: {'slug', 'ats', 'jobs'} or None."""
    best = None
    for slug in candidates(company):
        for ats, url in PROBES.items():
            async with sem:
                try:
                    r = await client.get(url.format(s=slug),
                                         headers={"User-Agent": USER_AGENT,
                                                  "Accept": "application/json"})
                except httpx.HTTPError:
                    continue
            if r.status_code != 200:
                continue
            try:
                jobs = _jobs(ats, r.json())
            except ValueError:
                continue
            if not jobs or not mentions(company, r.text):
                continue
            if best is None or len(jobs) > best["jobs"]:
                best = {"slug": slug, "ats": ats, "jobs": len(jobs)}
        if best:
            return best          # stop at the first slug that resolved; later guesses are weaker
    return None


def tag_existing(existing: list[dict], yc_companies: list[dict]) -> tuple[list[dict], list[dict]]:
    """Set yc: true on watch-list entries whose slug matches a YC company's candidates.
    Returns (YC companies NOT on the watch-list yet, entries newly tagged)."""
    by_slug = {}
    for c in existing:
        if str(c.get("ats", "")).lower() in AGGREGATORS:
            continue
        by_slug.setdefault(norm(str(c["slug"])), []).append(c)
    missing, tagged = [], []
    for yc in yc_companies:
        hits = [c for s in candidates(yc) for c in by_slug.get(norm(s), [])]
        if not hits:
            missing.append(yc)
            continue
        for c in hits:
            if not c.get("yc"):
                c["yc"] = True
                tagged.append(c)
    return missing, tagged


async def run(companies_path="config/companies.yaml", *, dry_run=False, max_companies=None,
              client=None) -> dict:
    data = yaml.safe_load(Path(companies_path).read_text()) or {"companies": []}
    existing = data.setdefault("companies", [])
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True)
    try:
        resp = await client.get(YC_LIST, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        yc_companies = [c for c in resp.json() if c.get("status", "Active") == "Active"]
        if max_companies:
            yc_companies = yc_companies[:max_companies]
        missing, tagged = tag_existing(existing, yc_companies)
        sem = asyncio.Semaphore(16)   # polite to the ATS APIs
        found = await asyncio.gather(*[probe(client, c, sem) for c in missing])
    finally:
        if owns:
            await client.aclose()

    have = {(str(c["slug"]).lower(), str(c["ats"]).lower()) for c in existing}
    added = []
    for yc, hit in zip(missing, found):
        if not hit or (hit["slug"], hit["ats"]) in have:
            continue
        have.add((hit["slug"], hit["ats"]))
        rec = {"slug": hit["slug"], "ats": hit["ats"], "tier": "target", "yc": True}
        existing.append(rec)
        added.append((yc.get("name"), rec, hit["jobs"]))

    for name, rec, n in added:
        print(f"yc: add {name} -> {rec['ats']}:{rec['slug']} ({n} jobs)")
    for c in tagged:
        print(f"yc: tag {c['ats']}:{c['slug']}")
    stats = {"yc_hiring": len(yc_companies), "already_watched": len(yc_companies) - len(missing),
             "newly_tagged": len(tagged), "probed": len(missing), "added": len(added),
             "no_board_found": len(missing) - sum(1 for f in found if f)}
    print("job-radar YC:", stats)
    if not dry_run and (added or tagged):
        Path(companies_path).write_text(yaml.safe_dump(data, sort_keys=False))
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(prog="job_radar.yc")
    ap.add_argument("--companies", default="config/companies.yaml")
    ap.add_argument("--dry-run", action="store_true", help="print, don't write the watch-list")
    ap.add_argument("--max-companies", type=int, default=None)
    args = ap.parse_args(argv)
    asyncio.run(run(args.companies, dry_run=args.dry_run, max_companies=args.max_companies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
