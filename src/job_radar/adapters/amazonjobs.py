"""Amazon Jobs (amazon.jobs) - Amazon runs its own ATS with a public search JSON API
(verified live, July 2026). Same proprietary-ATS blind-spot class as Google Careers.

We query broadly for intern-flavored software roles and filter to Canada in parse()
(the API's country filter param proved unreliable; country_code on each job is not).
Modelled as a single pseudo-company (slug 'amazon') so the per-company silent-prime
absorbs the backlog. Descriptions arrive inline, so no enrich call is needed.
"""
from datetime import timezone

from dateutil import parser as dateparser

from ..models import Company, Posting
from .base import strip_html

API = ("https://www.amazon.jobs/en/search.json"
       "?base_query={query}&result_limit=100&offset={offset}")
QUERIES = ("software intern", "software co-op")
PAGES_PER_QUERY = 2
_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
       "Accept": "application/json"}


def _dt(value):
    """amazon.jobs dates are human-format ('November 4, 2025'), not ISO."""
    if not value:
        return None
    try:
        dt = dateparser.parse(str(value))
    except (ValueError, TypeError, OverflowError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def parse(payload) -> list[Posting]:
    out = []
    for j in (payload or {}).get("jobs") or []:
        if (j.get("country_code") or "").upper() != "CAN":
            continue                       # Canada-only user; the API country param lies
        jid = j.get("id") or j.get("id_icims")
        if not jid:
            continue
        path = j.get("job_path") or ""
        out.append(Posting(
            uid=f"amazonjobs:amazon:{jid}",
            ats="amazonjobs",
            company="amazon",              # == slug; pseudo-company like 'simplify'
            title=f"Amazon: {j.get('title', '') or ''}",
            location=j.get("normalized_location") or j.get("city") or "Canada",
            url=f"https://www.amazon.jobs{path}" if path.startswith("/") else path,
            posted_at=_dt(j.get("posted_date")),
            description=strip_html(j.get("description_short") or j.get("description") or ""),
            raw=j,
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    out, seen = [], set()
    for query in QUERIES:
        for page in range(PAGES_PER_QUERY):
            url = API.format(query=query.replace(" ", "+"), offset=page * 100)
            resp = await client.get(url, headers=_UA, timeout=30.0)
            if resp.status_code != 200:
                break
            batch = parse(resp.json())
            fresh = [p for p in batch if p.uid not in seen]
            seen.update(p.uid for p in fresh)
            out.extend(fresh)
            if len((resp.json().get("jobs") or [])) < 100:
                break                      # last page for this query
    return out
