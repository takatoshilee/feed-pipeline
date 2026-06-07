from ..models import Company, Posting
from .base import get_json, strip_html, to_dt

API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"


def parse(slug: str, payload: dict) -> list[Posting]:
    out = []
    for j in payload.get("jobs") or []:   # null-safe: {"jobs": null} on empty boards
        jid = j.get("id")
        if jid is None:
            continue                       # skip malformed entries instead of crashing the board
        loc = (j.get("location") or {}).get("name", "") or ""
        out.append(Posting(
            uid=f"greenhouse:{slug}:{jid}",
            ats="greenhouse",
            company=slug,
            title=j.get("title", "") or "",
            location=loc,
            url=j.get("absolute_url", "") or "",
            posted_at=to_dt(j.get("updated_at")),
            description=strip_html(j.get("content", "")),
            raw=j,
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    payload = await get_json(client, API.format(slug=company.slug))
    return parse(company.slug, payload)
