import httpx

from job_radar.models import Company
from job_radar.adapters import oracle

LIST = {"items": [{"SearchId": 1, "TotalJobsCount": 2, "requisitionList": [
    {"Id": "9738", "Title": "Software Developer Intern", "PostedDate": "2026-07-06",
     "PrimaryLocation": "Toronto, ON, Canada", "ShortDescriptionStr": ""},
    {"Id": "9551", "Title": "Cleaner", "PostedDate": "2026-07-05",
     "PrimaryLocation": "Winnipeg, MB, Canada"},
    {"Title": "No Id job"},   # missing Id -> skipped
]}]}

DETAIL = {"items": [{"ExternalDescriptionStr": "<p>Join us.</p>",
                     "ExternalResponsibilitiesStr": "<p>Ship code.</p>",
                     "ExternalQualificationsStr": "Python."}]}


def test_parse_maps_and_filters():
    out = oracle.parse("cva.fa.us1", "CX_3", LIST)
    assert len(out) == 2                                   # no-Id row dropped
    p = out[0]
    assert p.uid == "oracle:cva.fa.us1:9738"
    assert p.ats == "oracle"
    assert p.company == "cva.fa.us1"
    assert p.title == "Software Developer Intern"
    assert p.location == "Toronto, ON, Canada"
    assert p.url == "https://cva.fa.us1.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_3/job/9738"
    assert p.posted_at is not None and p.posted_at.year == 2026


def test_parse_null_safe():
    assert oracle.parse("h", "s", {}) == []
    assert oracle.parse("h", "s", {"items": []}) == []
    assert oracle.parse("h", "s", {"items": [{"requisitionList": None}]}) == []


async def test_fetch_needs_site():
    # no wd_site -> misconfigured -> empty, no request made
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        assert await oracle.fetch(client, Company(slug="cva.fa.us1", ats="oracle")) == []


async def test_fetch_uses_client():
    def handler(request):
        assert "recruitingCEJobRequisitions" in str(request.url)
        assert "CX_3" in str(request.url)
        return httpx.Response(200, json=LIST)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await oracle.fetch(client, Company(slug="cva.fa.us1", ats="oracle", wd_site="CX_3"))
    assert len(out) == 2


async def test_enrich_fills_description():
    def handler(request):
        assert "recruitingCEJobRequisitionDetails" in str(request.url)
        return httpx.Response(200, json=DETAIL)

    base = oracle.parse("cva.fa.us1", "CX_3", LIST)[0]
    assert base.description == ""
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        enriched = await oracle.enrich(client, base, Company(slug="cva.fa.us1", ats="oracle", wd_site="CX_3"))
    assert enriched.description == "Join us. Ship code. Python."
