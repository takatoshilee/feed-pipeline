import json
from pathlib import Path

import httpx

from job_radar.models import Company
from job_radar.adapters import lever

FIX = json.loads((Path(__file__).parent / "fixtures" / "lever_postings.json").read_text())


def test_parse_lever_list():
    postings = lever.parse("wealthsimple", FIX)
    assert len(postings) == 1
    p = postings[0]
    assert p.uid == "lever:wealthsimple:abc-123"
    assert p.title == "Software Developer Intern"
    assert p.location == "Toronto"
    assert p.url.endswith("/abc-123")
    assert p.posted_at is not None  # parsed from epoch ms
    assert p.description == "Join our team."


async def test_fetch_lever():
    def handler(request):
        assert "api.lever.co/v0/postings/wealthsimple" in str(request.url)
        return httpx.Response(200, json=FIX)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await lever.fetch(client, Company(slug="wealthsimple", ats="lever"))
    assert len(postings) == 1
