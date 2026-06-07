import json
from pathlib import Path

import httpx

from job_radar.models import Company
from job_radar.adapters import smartrecruiters as sr

FIX = json.loads((Path(__file__).parent / "fixtures" / "smartrecruiters_jobs.json").read_text())


def test_parse_normalizes_and_handles_remote_location():
    postings = sr.parse("ubisoft", FIX)
    assert len(postings) == 2
    p = postings[0]
    assert p.uid == "smartrecruiters:ubisoft:743999000000001"
    assert p.ats == "smartrecruiters"
    assert p.title == "Software Engineering Intern"
    assert p.location == "Montreal, QC, ca"
    assert p.url == "https://jobs.smartrecruiters.com/ubisoft/743999000000001"
    assert p.posted_at is not None and p.posted_at.year == 2026
    assert postings[1].location == "Remote"  # null city/region/country + remote flag


async def test_fetch_smartrecruiters():
    def handler(request):
        assert "api.smartrecruiters.com/v1/companies/ubisoft/postings" in str(request.url)
        return httpx.Response(200, json=FIX)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await sr.fetch(client, Company(slug="ubisoft", ats="smartrecruiters"))
    assert len(postings) == 2
