from ..models import Company, Posting
from .base import from_ms, get_json

API = "https://api.lever.co/v0/postings/{slug}?mode=json"


def parse(slug: str, payload: list) -> list[Posting]:
    out = []
    for j in payload or []:
        jid = j.get("id")
        if jid is None:
            continue
        cats = j.get("categories") or {}
        out.append(Posting(
            uid=f"lever:{slug}:{jid}",
            ats="lever",
            company=slug,
            title=j.get("text", "") or "",
            location=cats.get("location", "") or "",
            url=j.get("hostedUrl", "") or "",
            posted_at=from_ms(j.get("createdAt")),
            description=j.get("descriptionPlain", "") or "",
            raw=j,
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    payload = await get_json(client, API.format(slug=company.slug))
    return parse(company.slug, payload)
