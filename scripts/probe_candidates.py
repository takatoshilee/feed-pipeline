"""One-off: probe candidate startup slugs across Greenhouse/Lever/Ashby and report which
are real live boards (and how many jobs). Used to expand coverage with verified slugs.
Run: python scripts/probe_candidates.py  ->  prints CSV-ready 'slug,ats,tier' hit lines."""
import asyncio

import httpx
import yaml

# Candidate slugs (best-guess; the probe keeps only the ones that resolve to a live board).
# Deliberately niche / YC-flavored startups that hire early-career, not already tracked.
CANDIDATES = """
huggingface replicate octoml predibase lamini unstructuredio vectara nomic-ai cleanlab
weightsandbiases cometml lightningai characterai adept assemblyai hebbia imbue magic
codeium tabnine roboflow labelbox surgehq mercor turing micro1 datacurve perplexityai
retool airbyte dbtlabs hex getcensus fivetran montecarlodata warpdotdev raycast
thebrowsercompany valtown flyio planetscale turso xata convex liveblocks novu inngest
triggerdotdev depot workos stytch knock courier vanta drata secureframe materialsecurity
chainguard wiz snyk tailscale doppler infisical rippling justworks gem coda statsig
launchdarkly bun denoland astro remix hasura apollographql wundergraph grafbase prisma
tigerbeetle materialize risingwave clickhouse timescale questdb dolthub
unit column increase highnote lithic finix middesk alloy withpersona sardine moov dwolla
treasuryprime synctera pulley mainstreet puzzle capchase pipe settle finch checkout
oneX agilityrobotics collaborativerobotics bearrobotics dexterity covariant machinalabs
hadrian appliedintuition cruise wayve helsing saronic castelion stokespace varda
geckorobotics pathai standardbots formlabs markforged chefrobotics fieldai
ashbyhq mixpanel junehq incidentio rootly firehydrant opslevel getport zapier make
n8n pipedream merge nango getparagon clay attio folk twenty salesloft outreach gong
clari ironclad cultureamp 15five leapsome remotecom assembled frontapp intercom kustomer
gladly forethought observeai replicant parloa lindy
""".split()

ATS_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{s}/jobs",
    "lever": "https://api.lever.co/v0/postings/{s}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{s}",
}


def _count(ats, data):
    if ats == "greenhouse":
        return len(data.get("jobs") or [])
    if ats == "lever":
        return len(data) if isinstance(data, list) else 0
    return len((data or {}).get("jobs") or [])  # ashby


async def probe(client, slug, ats, sem):
    async with sem:
        try:
            r = await client.get(ATS_URL[ats].format(s=slug))
            if r.status_code != 200:
                return None
            return (slug, ats, _count(ats, r.json()))
        except Exception:
            return None


async def main():
    existing = {c["slug"].lower() for c in yaml.safe_load(open("config/companies.yaml"))["companies"]}
    cands = [s for s in dict.fromkeys(CANDIDATES) if s.lower() not in existing]
    sem = asyncio.Semaphore(24)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        tasks = [probe(client, s, ats, sem) for s in cands for ats in ATS_URL]
        results = [r for r in await asyncio.gather(*tasks) if r]
    # Prefer the ATS with the most jobs when a slug resolves on more than one.
    best = {}
    for slug, ats, n in results:
        if slug not in best or n > best[slug][1]:
            best[slug] = (ats, n)
    print(f"# probed {len(cands)} candidates; {len(best)} resolved to a live board\n")
    lines = [f"{slug},{ats},target" for slug, (ats, _n) in sorted(best.items())]
    with open("scripts/startups_seed.csv", "w") as f:
        f.write("# verified niche/YC startup boards (probe_candidates.py)\n" + "\n".join(lines) + "\n")
    for slug, (ats, n) in sorted(best.items(), key=lambda x: -x[1][1]):
        print(f"{slug},{ats}    # {n} jobs")
    print(f"\nwrote scripts/startups_seed.csv ({len(lines)} boards)")


if __name__ == "__main__":
    asyncio.run(main())
