import httpx

from job_radar.models import Company
from job_radar.adapters import amazonjobs

PAYLOAD = {"jobs": [
    {"id": "3120598", "title": "Software Development Engineer Intern",
     "country_code": "CAN", "normalized_location": "Toronto, ON, CAN",
     "job_path": "/en/jobs/3120598/sde-intern", "posted_date": "July 10, 2026",
     "description_short": "<p>Build <b>things</b> at scale.</p>"},
    # non-Canada -> filtered out
    {"id": "999", "title": "SDE Intern", "country_code": "BRA",
     "job_path": "/en/jobs/999/x", "posted_date": "July 1, 2026"},
    # missing id -> skipped, not a crash
    {"title": "Ghost", "country_code": "CAN"},
]}


def test_parse_filters_to_canada_and_maps():
    out = amazonjobs.parse(PAYLOAD)
    assert len(out) == 1
    p = out[0]
    assert p.uid == "amazonjobs:amazon:3120598"
    assert p.company == "amazon"                           # == slug: prime absorbs backlog
    assert p.title == "Amazon: Software Development Engineer Intern"
    assert p.location == "Toronto, ON, CAN"
    assert p.url == "https://www.amazon.jobs/en/jobs/3120598/sde-intern"
    assert p.description == "Build things at scale."       # html stripped
    assert p.posted_at is not None and p.posted_at.year == 2026


def test_parse_null_safe():
    assert amazonjobs.parse({}) == []
    assert amazonjobs.parse(None) == []


async def test_fetch_dedupes_across_queries():
    def handler(request):
        return httpx.Response(200, json=PAYLOAD)           # same job from every query

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await amazonjobs.fetch(client, Company(slug="amazon", ats="amazonjobs"))
    assert len(out) == 1                                    # deduped across query pages
