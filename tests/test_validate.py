import httpx

from job_radar.models import Company
from job_radar.validate import run_validate, is_dead, prune


def test_is_dead():
    assert is_dead("error:HTTPStatusError")
    assert is_dead("no-adapter")
    assert not is_dead("ok")
    assert not is_dead("empty")


async def test_run_validate_labels_each_board():
    companies = [
        Company(slug="stripe", ats="greenhouse"),     # -> ok
        Company(slug="dead", ats="greenhouse"),        # -> error (404)
        Company(slug="quietco", ats="ashby"),          # -> empty
        Company(slug="x", ats="nosuch"),               # -> no-adapter
    ]

    def handler(request):
        url = str(request.url)
        if "boards/stripe" in url:
            return httpx.Response(200, json={"jobs": [
                {"id": 1, "title": "Intern", "location": {"name": "Toronto"},
                 "absolute_url": "u", "updated_at": "2026-06-01T00:00:00Z", "content": "x"}]})
        if "boards/dead" in url:
            return httpx.Response(404)
        return httpx.Response(200, json={"jobs": []})  # ashby empty

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    results = await run_validate(companies, client=client)
    await client.aclose()

    by_slug = {c.slug: status for c, status, _ in results}
    assert by_slug["stripe"] == "ok"
    assert by_slug["dead"].startswith("error")
    assert by_slug["quietco"] == "empty"
    assert by_slug["x"] == "no-adapter"


def test_prune_drops_only_dead():
    companies = [Company(slug="a", ats="greenhouse"), Company(slug="b", ats="ashby")]
    results = [
        (companies[0], "ok", 5),
        (companies[1], "error:HTTPStatusError", 0),
    ]
    kept = prune(companies, results)
    assert [c.slug for c in kept] == ["a"]
