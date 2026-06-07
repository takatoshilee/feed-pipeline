import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ..models import Company, Posting
from .base import get_json, strip_html

# Per-tenant CXS endpoint. tenant = company.slug, host = company.wd_host (e.g. "wd5"),
# site = company.wd_site (e.g. "NVIDIAExternalCareerSite").
CXS = "https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
DETAIL = "https://{tenant}.{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}{ext}"


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
    for j in payload.get("jobPostings") or []:   # null-safe
        ext = j.get("externalPath", "") or ""
        if not ext:
            continue  # no stable id/url without externalPath
        out.append(Posting(
            uid=f"workday:{tenant}:{ext}",
            ats="workday",
            company=tenant,
            title=j.get("title", "") or "",
            location=j.get("locationsText", "") or "",
            url=f"{base_url}/{site}{ext}",
            posted_at=_posted_at(j.get("postedOn"), now),
            description="",  # Workday needs a second call for the body; score on title+location
            raw=j,
        ))
    return out


async def fetch(client, company: Company, *, now=None, page_limit=8, page_size=20) -> list[Posting]:
    # Workday quirks: `total` is reported only on page 1 (later pages send total=0 but
    # still return a full page), and the API rejects limit > 20. So we capture the
    # total once and stop only on a short/empty page or once that total is covered.
    if not company.wd_host or not company.wd_site:
        return []  # misconfigured workday entry: needs wd_host + wd_site
    now = now or datetime.now(timezone.utc)
    tenant, host, site = company.slug, company.wd_host, company.wd_site
    base_url = f"https://{tenant}.{host}.myworkdayjobs.com"
    url = CXS.format(tenant=tenant, host=host, site=site)

    out: list[Posting] = []
    total = None
    for page in range(page_limit):
        body = {"appliedFacets": {}, "limit": page_size, "offset": page * page_size, "searchText": ""}
        payload = await get_json(client, url, method="POST", json_body=body)
        batch = payload.get("jobPostings") or []
        out.extend(parse(tenant, site, base_url, payload, now))
        if total is None:
            total = payload.get("total") or 0
        full_page = len(batch) == page_size
        reached_total = bool(total) and (page + 1) * page_size >= total
        if not full_page or reached_total:
            break
    return out


async def enrich(client, posting: Posting, company: Company) -> Posting:
    """Fetch the full job description for a single posting (second call)."""
    ext = posting.raw.get("externalPath")
    if not ext or not company.wd_host or not company.wd_site:
        return posting
    url = DETAIL.format(tenant=company.slug, host=company.wd_host, site=company.wd_site, ext=ext)
    try:
        data = await get_json(client, url)
    except Exception:
        return posting
    desc = strip_html((data.get("jobPostingInfo") or {}).get("jobDescription", "") or "")
    return replace(posting, description=desc) if desc else posting
