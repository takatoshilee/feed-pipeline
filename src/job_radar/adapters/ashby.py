from ..models import Company, Posting
from .base import get_json, strip_html, to_dt

API = "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"


def parse(slug: str, payload: dict) -> list[Posting]:
    out = []
    for j in payload.get("jobs") or []:   # null-safe
        if j.get("isListed") is False:
            continue
        jid = j.get("id")
        if jid is None:
            continue
        desc = j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", ""))
        out.append(Posting(
            uid=f"ashby:{slug}:{jid}",
            ats="ashby",
            company=slug,
            title=j.get("title", "") or "",
            location=j.get("location", "") or "",
            url=j.get("jobUrl", "") or j.get("applyUrl", "") or "",
            posted_at=to_dt(j.get("publishedAt")),
            description=desc or "",
            raw=j,
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    payload = await get_json(client, API.format(slug=company.slug))
    return parse(company.slug, payload)
