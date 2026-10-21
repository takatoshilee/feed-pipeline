import httpx
import pytest

from job_radar.adapters import canadalist
from job_radar.models import Company

README = """# list

| Company | Role | Location | Apply | Date Posted |
|--------|------|----------|:-----:|--------------|
| HeyGen | Software Engineer Intern (Summer 2027) | Toronto, ON | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://job-boards.greenhouse.io/heygen/jobs/1) | Oct 19, 2026 |
| ↳ | ML Intern | Remote, Canada | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://job-boards.greenhouse.io/heygen/jobs/2) | Oct 18, 2026 | 
| Sun Life | AI Developer Student Intern | Toronto, ON | Closed🔒 | Oct 13, 2026 |
| ↳ | Data Intern | Toronto, ON | [![Apply](https://img.shields.io/badge/-Apply-blue)](https://sunlife.example/3) | bad date |
"""


def test_parse_maps_rows_and_skips_closed():
    ps = canadalist.parse(README)
    assert [p.title for p in ps] == ["HeyGen: Software Engineer Intern (Summer 2027)",
                                     "HeyGen: ML Intern", "Sun Life: Data Intern"]
    p = ps[0]
    assert p.ats == "canadalist" and p.company == "canadalist"
    assert p.uid.startswith("canadalist:canadalist:")
    assert p.url == "https://job-boards.greenhouse.io/heygen/jobs/1"
    assert p.location == "Toronto, ON"
    assert p.posted_at is not None and (p.posted_at.year, p.posted_at.month) == (2026, 10)
    assert ps[2].posted_at is None
    assert len({p.uid for p in ps}) == 3


def test_parse_empty():
    assert canadalist.parse("") == []


@pytest.mark.asyncio
async def test_fetch():
    def handler(req):
        assert "Canadian-Tech-Internships-2027" in str(req.url)
        return httpx.Response(200, text=README)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ps = await canadalist.fetch(client, Company(slug="canadalist", ats="canadalist"))
    assert len(ps) == 3
