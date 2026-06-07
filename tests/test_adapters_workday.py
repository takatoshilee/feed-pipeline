import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx

from job_radar.models import Company
from job_radar.adapters import workday

FIX = json.loads((Path(__file__).parent / "fixtures" / "workday_jobs.json").read_text())
NOW = datetime(2026, 6, 6, 12, tzinfo=timezone.utc)
BASE = "https://nvidia.wd5.myworkdayjobs.com"
COMPANY = Company(slug="nvidia", ats="workday", wd_host="wd5", wd_site="NVIDIAExternalCareerSite")


def test_parse_builds_uid_url_and_relative_dates():
    postings = workday.parse("nvidia", "NVIDIAExternalCareerSite", BASE, FIX, NOW)
    assert len(postings) == 2
    p = postings[0]
    assert p.uid == "workday:nvidia:/job/Toronto-ON/Software-Engineer-Intern_R-1001"
    assert p.ats == "workday"
    assert p.title == "Software Engineer Intern"
    assert p.location == "Toronto, ON, Canada"
    assert p.url == f"{BASE}/NVIDIAExternalCareerSite/job/Toronto-ON/Software-Engineer-Intern_R-1001"
    assert p.posted_at == NOW                                  # "Posted Today"
    assert postings[1].posted_at == NOW - timedelta(days=10)   # "Posted 10 Days Ago"


async def test_fetch_single_page():
    def handler(request):
        assert "/wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs" in str(request.url)
        return httpx.Response(200, json=FIX)  # total=2 -> stops after one page

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await workday.fetch(client, COMPANY, now=NOW)
    assert len(postings) == 2


async def test_fetch_paginates():
    def page(offset, n):
        return {"total": 25, "jobPostings": [
            {"title": f"Engineer {offset + i}", "externalPath": f"/job/x/R-{offset + i}",
             "locationsText": "Remote", "postedOn": "Posted Today"} for i in range(n)
        ]}

    def handler(request):
        offset = json.loads(request.read().decode())["offset"]
        return httpx.Response(200, json=page(offset, 20 if offset == 0 else 5))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await workday.fetch(client, COMPANY, now=NOW, page_size=20)
    assert len(postings) == 25  # 20 + 5 across two pages


async def test_fetch_returns_empty_when_misconfigured():
    company = Company(slug="x", ats="workday")  # no wd_host/wd_site
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=FIX))) as client:
        postings = await workday.fetch(client, company, now=NOW)
    assert postings == []


async def test_enrich_fills_description():
    detail = {"jobPostingInfo": {"jobDescription": "&lt;p&gt;Build &lt;b&gt;AI&lt;/b&gt; systems.&lt;/p&gt;"}}

    def handler(request):
        assert "/wday/cxs/nvidia/NVIDIAExternalCareerSite/job/" in str(request.url)
        return httpx.Response(200, json=detail)

    posting = workday.parse("nvidia", "NVIDIAExternalCareerSite", BASE, FIX, NOW)[0]
    assert posting.description == ""  # empty before enrichment
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enriched = await workday.enrich(client, posting, COMPANY)
    assert "Build AI systems" in enriched.description
    assert enriched.uid == posting.uid  # identity preserved
