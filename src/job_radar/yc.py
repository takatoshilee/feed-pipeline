"""Y Combinator startups: find the job boards of YC companies that are hiring, add them to
the watch-list, and tag them `yc: true` so the Top 30 ranks their roles up.

Company list: the yc-oss mirror of YC's public company directory (hiring.json, rebuilt
daily from ycombinator.com/companies). For each company we guess board slugs from its YC
slug, its name and its website, and probe the ATS APIs the radar already polls (Ashby,
Greenhouse, Lever). A guess only counts with proof that the board is that company's: its
website domain in the postings or on the hosted board page, or, for a distinctive name
like "HackerRank", the name in the postings. A plain-word slug ("pulse", "atlas") with
no website proof is skipped, and so is a board with far more openings than the company
has people.

Why not poll ycombinator.com/companies/<slug>/jobs directly: YC answers HTTP 429 after
about 60 page loads (tested Nov 18 2026), so 1,400 company pages every 15 minutes is not
an option. Companies that only post on Work at a Startup stay uncovered here.

Existing watch-list entries that match a YC company get `yc: true` added, with the same
proof required when the name is a plain word.
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


PAGES = {   # the hosted board page, which names the company's own website
    "ashby": "https://jobs.ashbyhq.com/{s}",
    "greenhouse": "https://job-boards.greenhouse.io/{s}",
    "lever": "https://jobs.lever.co/{s}",
}


def mentions_domain(company: dict, text: str) -> bool:
    dom = _domain(company.get("website", ""))
    if not dom:
        return False
    # whole domain only: 'pulse.co' must not match 'pulse.com' or 'impulse.co'
    t = text.lower()
    pat = r"(?<![a-z0-9-])" + re.escape(dom) + r"(?![a-z0-9-]|\.[a-z])"
    for m in re.finditer(pat, t):
        i = m.start()
        if i == 0 or t[i - 1] != "." or t[:i].endswith("www."):
            return True          # 'x.pulse.co' is another site; 'www.pulse.co' is theirs
    return False


def distinctive(name: str) -> bool:
    """'HackerRank', 'Apollo.io', 'Y Combinator 2' yes; 'Pulse', 'Atlas', 'Coast' no.
    A plain word can be any company's board slug, so it needs the website as proof."""
    name = (name or "").strip()
    return bool(re.search(r"[A-Z]", name[1:]) or re.search(r"[^A-Za-z]", name))


def too_big(company: dict, n_jobs: int) -> bool:
    """A board with far more openings than the company has people is someone else's."""
    size = company.get("team_size") or 0
    return bool(size) and n_jobs > max(40, 3 * int(size))


async def _get(client, url, sem):
    async with sem:
        try:
            return await client.get(url, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError:
            return None


async def verified(client, company: dict, ats: str, slug: str, payload_text: str, sem) -> bool:
    """Is this live board really this company's? Proof, strongest first: the company's
    domain in the postings, the domain on the hosted board page, or (distinctive names
    only) the name in the postings."""
    if mentions_domain(company, payload_text):
        return True
    r = await _get(client, PAGES[ats].format(s=slug), sem)
    if r is not None and r.status_code == 200 and mentions_domain(company, r.text):
        return True
    name = (company.get("name") or "").strip()
    if distinctive(name):
        pat = r"(?<![a-z0-9])" + re.escape(name.lower()) + r"(?![a-z0-9])"
        return re.search(pat, payload_text.lower()) is not None
    return False


async def probe(client, company: dict, sem) -> dict | None:
    """The best live board for one company: {'slug', 'ats', 'jobs'} or None."""
    best = None
    for slug in candidates(company):
        for ats, url in PROBES.items():
            r = await _get(client, url.format(s=slug), sem)
            if r is None or r.status_code != 200:
                continue
            try:
                jobs = _jobs(ats, r.json())
            except ValueError:
                continue
            if not jobs or too_big(company, len(jobs)):
                continue
            if best is not None and len(jobs) <= best["jobs"]:
                continue
            if await verified(client, company, ats, slug, r.text, sem):
                best = {"slug": slug, "ats": ats, "jobs": len(jobs)}
        if best:
            return best          # stop at the first slug that resolved; later guesses are weaker
    return None


def match_existing(existing: list[dict], yc_companies: list[dict]):
    """Pair YC companies with watch-list entries whose slug matches one of their
    candidates. Returns (YC companies NOT on the watch-list, [(yc_company, entry), ...])."""
    by_slug = {}
    for c in existing:
        if str(c.get("ats", "")).lower() in AGGREGATORS:
            continue
        by_slug.setdefault(norm(str(c["slug"])), []).append(c)
    missing, pairs = [], []
    for yc in yc_companies:
        hits = []
        for s in candidates(yc):
            for c in by_slug.get(norm(s), []):
                if c not in hits:
                    hits.append(c)
        if not hits:
            missing.append(yc)
        pairs.extend((yc, c) for c in hits)
    return missing, pairs


async def confirm(client, company: dict, entry: dict, sem) -> bool:
    """Should this existing entry get yc: true? A distinctive name is enough; a plain
    word ('radar', 'magic') needs the same website proof as a new board."""
    if distinctive(company.get("name", "")):
        return True
    ats, slug = str(entry["ats"]).lower(), str(entry["slug"])
    if ats not in PROBES:
        return False             # Workday etc.: no cheap proof, leave it untagged
    r = await _get(client, PROBES[ats].format(s=slug), sem)
    if r is None or r.status_code != 200:
        return False
    return await verified(client, company, ats, slug, r.text, sem)


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
        missing, pairs = match_existing(existing, yc_companies)
        sem = asyncio.Semaphore(16)   # polite to the ATS APIs
        oks = await asyncio.gather(*[confirm(client, y, e, sem) for y, e in pairs])
        tagged = []
        for (_, entry), ok in zip(pairs, oks):
            if ok and not entry.get("yc") and entry not in tagged:
                entry["yc"] = True
                tagged.append(entry)
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
             "matched_not_proven": sum(1 for ok in oks if not ok),
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
