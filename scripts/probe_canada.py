"""Probe a curated set of Canadian + remote-first companies across Greenhouse/Lever/Ashby
and keep the live boards. Higher ROI for a Canada-based candidate than dumping the
US-centric mega-lists. Run: python scripts/probe_canada.py  -> writes scripts/canada_seed.csv"""
import asyncio

import yaml

from probe_candidates import ATS_URL, probe  # reuse the same probe + URL map
import httpx

# Canadian tech + remote-first companies that hire early-career (best-guess slugs).
CANDIDATES = """
clio lightspeedhq vidyard jobber wattpad applyboard benevity symend tealbook relayfi
thinkific unbounce klue dialpad vena koho borrowell nuvei trulioo visier dapperlabs
hopper absorblms plooto wavehq paystone 1password agilebits fellowapp mejuri ssense knix
vendasta certn rewind clearco introhive alida deeplite darwinai layer6 sanctuaryai
kindred attabotics jane vosyn properly procurify thoughtexchange clearbanc cohereinc
 applyboardinc fellow getmux gatewaybio coconutsoftware coconut hiboutik tractian
automattic doist buffer loom toptal close hotjar fingerprintjs postscript remotecom
crossbeam mux paddle gitbook tability hopin float later
""".split()


async def main():
    existing = {c["slug"].lower() for c in yaml.safe_load(open("config/companies.yaml"))["companies"]}
    cands = [s for s in dict.fromkeys(CANDIDATES) if s.lower() not in existing]
    sem = asyncio.Semaphore(24)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        tasks = [probe(client, s, ats, sem) for s in cands for ats in ATS_URL]
        results = [r for r in await asyncio.gather(*tasks) if r]
    best = {}
    for slug, ats, n in results:
        if slug not in best or n > best[slug][1]:
            best[slug] = (ats, n)
    print(f"# probed {len(cands)} Canadian/remote candidates; {len(best)} live\n")
    with open("scripts/canada_seed.csv", "w") as f:
        f.write("# Canadian + remote-first boards (probe_canada.py)\n")
        for slug, (ats, _n) in sorted(best.items()):
            f.write(f"{slug},{ats},target\n")
    for slug, (ats, n) in sorted(best.items(), key=lambda x: -x[1][1]):
        print(f"{slug},{ats}    # {n} jobs")


if __name__ == "__main__":
    asyncio.run(main())
