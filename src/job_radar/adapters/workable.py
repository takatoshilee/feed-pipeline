from ..models import Company, Posting
from .base import get_json, strip_html, to_dt

# Public embed/widget API. `details=true` returns each job's full description inline,
# so Workable needs no second enrich call (unlike Workday/SmartRecruiters).
API = "https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"


def _location(j: dict) -> str:
    parts = [j.get("city"), j.get("state"), j.get("country")]
    loc = ", ".join(p for p in parts if p)
    if j.get("telecommuting"):
        return "Remote" if not loc else f"Remote / {loc}"
    return loc


def parse(slug: str, payload: dict) -> list[Posting]:
    out = []
    for j in (payload or {}).get("jobs") or []:   # null-safe on empty/closed accounts
        code = j.get("shortcode")
        if not code:
            continue                              # no stable id/url without a shortcode
        out.append(Posting(
            uid=f"workable:{slug}:{code}",
            ats="workable",
            company=slug,
            title=j.get("title", "") or "",
            location=_location(j),
            url=j.get("shortlink") or j.get("url") or j.get("application_url", "") or "",
            posted_at=to_dt(j.get("published_on") or j.get("created_at")),
            description=strip_html(j.get("description", "") or ""),
            raw=j,
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    payload = await get_json(client, API.format(slug=company.slug))
    return parse(company.slug, payload)
