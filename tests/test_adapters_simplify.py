import httpx

from job_radar.models import Company
from job_radar.adapters import simplify

LISTINGS = [
    # active Canadian bachelor's role -> kept
    {"id": "a1", "company_name": "Later", "title": "Data Science Co-op",
     "active": True, "is_visible": True, "sponsorship": "Other",
     "degrees": ["Bachelor's"], "locations": ["Vancouver, BC, Canada"],
     "url": "https://example.com/a1", "date_posted": 1762179937},
    # inactive (filled) -> skipped
    {"id": "a2", "company_name": "Acme", "title": "SWE Intern", "active": False,
     "is_visible": True, "degrees": ["Bachelor's"], "locations": ["Toronto, ON, Canada"],
     "url": "u", "date_posted": 1762179937},
    # PhD-only -> skipped
    {"id": "a3", "company_name": "Huawei", "title": "Intern Researcher", "active": True,
     "is_visible": True, "degrees": ["PhD"], "locations": ["Markham, ON, Canada"],
     "url": "u", "date_posted": 1762179937},
    # needs US citizenship -> skipped (international student can't get it)
    {"id": "a4", "company_name": "Defense Co", "title": "SWE Intern", "active": True,
     "is_visible": True, "sponsorship": "U.S. Citizenship is Required",
     "degrees": ["Bachelor's"], "locations": ["Austin, TX"], "url": "u", "date_posted": 1762179937},
    # missing id -> skipped, not a crash
    {"company_name": "NoId", "title": "x", "active": True, "is_visible": True},
]


def test_parse_filters_and_maps():
    postings = simplify.parse(LISTINGS)
    assert len(postings) == 1                  # only the active Canadian bachelor's role
    p = postings[0]
    assert p.uid == "simplify:simplify:a1"     # slug segment 'simplify' => prime absorbs it
    assert p.ats == "simplify"
    assert p.company == "simplify"             # == slug; the real name goes in the title
    assert p.title == "Later: Data Science Co-op"
    assert p.location == "Vancouver, BC, Canada"
    assert p.url == "https://example.com/a1"
    assert p.posted_at is not None and p.posted_at.year == 2025  # unix SECONDS, not ms


def test_parse_keeps_bachelor_among_mixed_degrees():
    # open to Bachelor's OR Master's is NOT grad-only -> kept
    out = simplify.parse([{"id": "m1", "company_name": "Co", "title": "Intern",
                           "active": True, "is_visible": True,
                           "degrees": ["Bachelor's", "Master's"], "locations": ["Toronto"],
                           "url": "u", "date_posted": 1762179937}])
    assert len(out) == 1


def test_parse_null_safe():
    assert simplify.parse(None) == []
    assert simplify.parse([]) == []


async def test_fetch_pulls_all_feeds_and_dedupes():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json=LISTINGS)   # same listing id from every feed

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await simplify.fetch(client, Company(slug="simplify", ats="simplify"))
    assert len(calls) == len(simplify.FEEDS)        # every season feed polled
    assert len(postings) == 1                        # same id across feeds -> deduped


async def test_fetch_survives_a_missing_feed():
    # SimplifyJobs' 2027 repo doesn't exist yet: its feed 404s. The others still serve.
    def handler(request):
        if "SimplifyJobs/Summer2027" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, json=LISTINGS)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        postings = await simplify.fetch(client, Company(slug="simplify", ats="simplify"))
    assert len(postings) == 1                        # dead feed skipped, source still alive
