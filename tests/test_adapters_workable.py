import httpx

from job_radar.models import Company
from job_radar.adapters import workable

PAYLOAD = {
    "name": "DataVisor",
    "jobs": [
        {"title": "Software Engineer Intern", "shortcode": "ABC123", "telecommuting": True,
         "city": "Vancouver", "state": "British Columbia", "country": "Canada",
         "shortlink": "https://apply.workable.com/j/ABC123",
         "application_url": "https://apply.workable.com/j/ABC123/apply",
         "published_on": "2026-06-09", "created_at": "2026-06-01",
         "description": "<p>Build <b>cool</b> things.</p>"},
        # on-site role: no telecommuting prefix
        {"title": "Data Analyst", "shortcode": "DEF456", "telecommuting": False,
         "city": "Toronto", "state": "Ontario", "country": "Canada",
         "url": "https://apply.workable.com/j/DEF456", "published_on": "2026-05-01",
         "description": ""},
        # missing shortcode -> skipped, not a crash
        {"title": "Ghost", "telecommuting": False, "city": "Nowhere"},
    ],
}


def test_parse_maps_and_filters():
    out = workable.parse("datavisor-jobs", PAYLOAD)
    assert len(out) == 2                                  # ghost (no shortcode) dropped
    p = out[0]
    assert p.uid == "workable:datavisor-jobs:ABC123"
    assert p.ats == "workable"
    assert p.company == "datavisor-jobs"
    assert p.title == "Software Engineer Intern"
    assert p.location == "Remote / Vancouver, British Columbia, Canada"  # telecommuting prefix
    assert p.url == "https://apply.workable.com/j/ABC123"                 # shortlink preferred
    assert p.description == "Build cool things."                         # html stripped
    assert p.posted_at is not None and p.posted_at.year == 2026


def test_parse_onsite_location_has_no_remote_prefix():
    out = workable.parse("x", PAYLOAD)
    assert out[1].location == "Toronto, Ontario, Canada"


def test_parse_null_safe():
    assert workable.parse("x", {}) == []
    assert workable.parse("x", {"jobs": None}) == []


async def test_fetch_uses_client():
    def handler(request):
        assert "datavisor-jobs" in str(request.url)
        return httpx.Response(200, json=PAYLOAD)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await workable.fetch(client, Company(slug="datavisor-jobs", ats="workable"))
    assert len(out) == 2
