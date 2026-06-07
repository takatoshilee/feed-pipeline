import json
from pathlib import Path

import httpx

from job_radar.models import Company
from job_radar.adapters import greenhouse

FIX = json.loads((Path(__file__).parent / "fixtures" / "greenhouse_jobs.json").read_text())


def test_parse_normalizes_postings():
    postings = greenhouse.parse("stripe", FIX)
    assert len(postings) == 2
    p = postings[0]
    assert p.uid == "greenhouse:stripe:101"
    assert p.ats == "greenhouse"
    assert p.title == "Software Engineer Intern"
    assert p.location == "Toronto, Canada"
    assert p.url.endswith("/101")
    assert p.posted_at is not None and p.posted_at.year == 2026
    assert "Build things" in p.description  # html stripped + unescaped


def test_parse_null_safe_and_skips_missing_id():
    assert greenhouse.parse("x", {"jobs": None}) == []   # {"jobs": null} on an empty board
    assert greenhouse.parse("x", {}) == []
    postings = greenhouse.parse("x", {"jobs": [
        {"title": "no id"},  # malformed entry -> skipped, not a crash
        {"id": 5, "title": "ok", "location": {"name": "T"},
         "absolute_url": "u", "updated_at": "2026-06-01T00:00:00Z", "content": "c"},
    ]})
    assert len(postings) == 1 and postings[0].uid == "greenhouse:x:5"


async def test_fetch_uses_client():
    def handler(request):
        assert "boards-api.greenhouse.io/v1/boards/stripe/jobs" in str(request.url)
        return httpx.Response(200, json=FIX)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await greenhouse.fetch(client, Company(slug="stripe", ats="greenhouse"))
    assert len(postings) == 2
