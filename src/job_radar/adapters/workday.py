import re
from datetime import datetime, timedelta, timezone

from ..models import Company, Posting
from .base import get_json

# Per-tenant CXS endpoint. tenant = company.slug, host = company.wd_host (e.g. "wd5"),
# site = company.wd_site (e.g. "NVIDIAExternalCareerSite").
CXS = "https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"


def _posted_at(posted_on: str | None, now: datetime) -> datetime | None:
    """Workday exposes relative text ('Posted Today', 'Posted 5 Days Ago'). Approximate it."""
    if not posted_on:
        return None
    s = posted_on.lower()
    if "today" in s:
        return now
    if "yesterday" in s:
        return now - timedelta(days=1)
    m = re.search(r"(\d+)\+?\s*day", s)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\+?\s*month", s)
    if m:
        return now - timedelta(days=30 * int(m.group(1)))
    return None


def parse(tenant: str, site: str, base_url: str, payload: dict,
          now: datetime | None = None) -> list[Posting]:
    now = now or datetime.now(timezone.utc)
    out = []
    for j in payload.get("jobPostings", []):
        ext = j.get("externalPath", "") or ""
        out.append(Posting(
            uid=f"workday:{tenant}:{ext or j.get('title', '')}",
            ats="workday",
            company=tenant,
            title=j.get("title", "") or "",
            location=j.get("locationsText", "") or "",
            url=f"{base_url}/{site}{ext}" if ext else base_url,
            posted_at=_posted_at(j.get("postedOn"), now),
            description="",  # Workday needs a second call for the body; score on title+location
            raw=j,
        ))
    return out


async def fetch(client, company: Company, *, now=None, page_limit=5, page_size=20) -> list[Posting]:
    if not company.wd_host or not company.wd_site:
        return []  # misconfigured workday entry: needs wd_host + wd_site
    now = now or datetime.now(timezone.utc)
    tenant, host, site = company.slug, company.wd_host, company.wd_site
    base_url = f"https://{tenant}.{host}.myworkdayjobs.com"
    url = CXS.format(tenant=tenant, host=host, site=site)

    out: list[Posting] = []
    for page in range(page_limit):
        body = {"appliedFacets": {}, "limit": page_size, "offset": page * page_size, "searchText": ""}
        payload = await get_json(client, url, method="POST", json_body=body)
        batch = parse(tenant, site, base_url, payload, now)
        out.extend(batch)
        total = payload.get("total", 0)
        if not payload.get("jobPostings") or (page + 1) * page_size >= total:
            break
    return out
