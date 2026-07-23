import httpx

from job_radar.models import Company
from job_radar.adapters import googlecareers

# Real page shape: each job link repeats several times in the rendered HTML/JS blob.
HTML = """
<a href="jobs/results/76982475250639558-software-developer-intern-bs-summer-2027">x</a>
noise "jobs/results/76982475250639558-software-developer-intern-bs-summer-2027" noise
<a href="jobs/results/113855697199735494-student-researcher-bsms-fall-2026">y</a>
"""


def test_parse_extracts_and_dedupes():
    out = googlecareers.parse(HTML)
    assert len(out) == 2                                   # repeated link deduped
    p = out[0]
    assert p.uid == "googlecareers:google:76982475250639558"
    assert p.ats == "googlecareers"
    assert p.company == "google"                           # == slug: prime absorbs backlog
    assert p.title == "Google: Software Developer Intern BS Summer 2027"
    assert p.location == "Canada"
    assert p.url.endswith("76982475250639558-software-developer-intern-bs-summer-2027")


def test_parse_null_safe():
    assert googlecareers.parse("") == []
    assert googlecareers.parse(None) == []


async def test_fetch_paginates_and_stops_on_repeat():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=HTML)              # page 2 repeats page 1 -> stop

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        out = await googlecareers.fetch(client, Company(slug="google", ats="googlecareers"))
    assert len(out) == 2
    assert len(calls) == 2                                 # fetched page 2, saw repeats, stopped
