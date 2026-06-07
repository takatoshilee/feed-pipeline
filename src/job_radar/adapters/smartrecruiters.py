from ..models import Company, Posting
from .base import get_json, to_dt

API = "https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit={limit}&offset={offset}"


def _location(loc: dict) -> str:
    parts = [loc.get("city"), loc.get("region"), loc.get("country")]
    text = ", ".join([p for p in parts if p])
    if not text and loc.get("remote"):
        return "Remote"
    return text


def parse(slug: str, payload: dict) -> list[Posting]:
    out = []
    for j in payload.get("content", []):
        out.append(Posting(
            uid=f"smartrecruiters:{slug}:{j['id']}",
            ats="smartrecruiters",
            company=slug,
            title=j.get("name", "") or "",
            location=_location(j.get("location") or {}),
            url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
            posted_at=to_dt(j.get("releasedDate") or j.get("createdOn")),
            description="",  # SmartRecruiters detail needs a 2nd call; score on title+location
            raw=j,
        ))
    return out


async def fetch(client, company: Company, *, page_limit=5, page_size=100) -> list[Posting]:
    out: list[Posting] = []
    for page in range(page_limit):
        url = API.format(slug=company.slug, limit=page_size, offset=page * page_size)
        payload = await get_json(client, url)
        out.extend(parse(company.slug, payload))
        total = payload.get("totalFound", 0)
        if not payload.get("content") or (page + 1) * page_size >= total:
            break
    return out
