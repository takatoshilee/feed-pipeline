import json
from pathlib import Path

import httpx

from job_radar.models import Company
from job_radar.adapters import ashby

FIX = json.loads((Path(__file__).parent / "fixtures" / "ashby_jobs.json").read_text())


def test_parse_ashby_skips_unlisted():
    postings = ashby.parse("cohere", FIX)
    assert len(postings) == 1  # job_2 isListed false is dropped
    p = postings[0]
    assert p.uid == "ashby:cohere:job_1"
    assert p.title == "ML Engineering Intern"
    assert p.location == "Remote, Canada"
    assert p.posted_at is not None
    assert p.description == "Work on models."


async def test_fetch_ashby():
    def handler(request):
        assert "api.ashbyhq.com/posting-api/job-board/cohere" in str(request.url)
        return httpx.Response(200, json=FIX)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await ashby.fetch(client, Company(slug="cohere", ats="ashby"))
    assert len(postings) == 1
