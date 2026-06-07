"""Mine the well-known internship-list repos for company ATS slugs we don't already
track. The lists themselves are downstream of the ATS boards we poll directly, but
they're a great DISCOVERY source: their apply URLs point at Greenhouse/Lever/Ashby
boards we can add to direct polling. Outputs candidate 'slug,ats' lines (validate later).
Run: python scripts/mine_lists.py"""
import re

import httpx
import yaml

# listings.json (structured) from the big trackers; several branch/path variants tried.
SOURCES = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/main/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/main/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/cvrve/Summer2026-Internships/main/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
]

# URL -> (ats, slug). Order matters; first match wins.
PATTERNS = [
    ("greenhouse", re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"([a-z0-9_-]+)\.greenhouse\.io", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"ashbyhq\.com/([a-z0-9_-]+)/jobs", re.I)),
]
JUNK = {"embed", "job_app", "for", "www", "jobs", "careers"}


def extract(url):
    for ats, pat in PATTERNS:
        m = pat.search(url or "")
        if m:
            slug = m.group(1).lower()
            if slug not in JUNK and len(slug) > 1:
                return ats, slug
    return None


def main():
    existing = {c["slug"].lower() for c in yaml.safe_load(open("config/companies.yaml"))["companies"]}
    found = {}  # slug -> ats
    seen_urls = 0
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        for src in SOURCES:
            try:
                r = client.get(src)
                if r.status_code != 200:
                    continue
                data = r.json()
            except Exception as e:
                print(f"# skip {src.split('/')[4]} ({type(e).__name__})")
                continue
            entries = data if isinstance(data, list) else data.get("listings", [])
            for e in entries:
                url = e.get("url") or e.get("apply_link") or e.get("company_url") or ""
                seen_urls += 1
                hit = extract(url)
                if hit:
                    ats, slug = hit
                    if slug not in existing:
                        found.setdefault(slug, ats)
            print(f"# {src.split('/')[4]}@{src.split('/')[6]}: ok ({len(entries)} entries)")

    print(f"\n# scanned {seen_urls} urls; {len(found)} new candidate slugs not already tracked\n")
    with open("scripts/mined_seed.csv", "w") as f:
        f.write("# mined from internship-list repos (mine_lists.py)\n")
        for slug, ats in sorted(found.items()):
            f.write(f"{slug},{ats},target\n")
    print(f"wrote scripts/mined_seed.csv ({len(found)} candidates) -- probe/validate before trusting")


if __name__ == "__main__":
    main()
