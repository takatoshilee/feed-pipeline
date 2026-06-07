"""Manual smoke test: hits one real board per ATS. Run: python scripts/smoke.py"""
import asyncio

import httpx

from job_radar.models import Company
from job_radar.adapters import ashby, greenhouse, lever, smartrecruiters, workday


async def main():
    async with httpx.AsyncClient() as client:
        checks = [
            (greenhouse, Company(slug="stripe", ats="greenhouse")),
            (lever, Company(slug="wealthsimple", ats="lever")),
            (ashby, Company(slug="cohere", ats="ashby")),
            (smartrecruiters, Company(slug="ubisoft", ats="smartrecruiters")),
            (workday, Company(slug="nvidia", ats="workday", wd_host="wd5",
                              wd_site="NVIDIAExternalCareerSite")),
        ]
        for adapter, company in checks:
            try:
                postings = await adapter.fetch(client, company)
                print(f"{company.ats:16} {company.slug:14} -> {len(postings)} postings")
                if postings:
                    print(f"                 e.g. {postings[0].title} | {postings[0].location}")
            except Exception as e:
                print(f"{company.ats:16} {company.slug:14} -> ERROR {e!r}")


if __name__ == "__main__":
    asyncio.run(main())
