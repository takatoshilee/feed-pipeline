import httpx

from job_radar.models import Company
from job_radar import sources


async def test_fetch_all_dispatches_and_tolerates_errors():
    companies = [
        Company(slug="stripe", ats="greenhouse"),
        Company(slug="wealthsimple", ats="lever"),
        Company(slug="bogus", ats="unknown_ats"),  # no adapter -> skipped
    ]

    def handler(request):
        url = str(request.url)
        if "greenhouse" in url:
            return httpx.Response(200, json={"jobs": [
                {"id": 1, "title": "Intern", "location": {"name": "Toronto"},
                 "absolute_url": "u", "updated_at": "2026-06-01T00:00:00Z", "content": "x"}]})
        if "lever.co" in url:
            return httpx.Response(500)  # this board errors
        return httpx.Response(404)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    postings, errors = await sources.fetch_all(companies, client=client)
    await client.aclose()

    assert len(postings) == 1                 # greenhouse succeeded
    assert any(slug == "wealthsimple" for slug, _ in errors)  # lever 500 captured
