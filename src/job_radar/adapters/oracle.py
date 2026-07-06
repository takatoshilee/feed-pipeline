"""Oracle Recruiting Cloud (the Fusion / "CandidateExperience" ATS). Anonymous JSON is
served from each customer's Fusion pod. Like Workday it needs two coordinates: a HOST code
(e.g. "cva.fa.us1") and a SITE number (e.g. "CX_3"). We store the host as company.slug and
the site as company.wd_site (reusing Workday's second-coordinate field), so:

    https://{slug}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions

The `expand=requisitionList` is required or the list comes back empty (the bare call returns
only facet/search metadata). Descriptions arrive inline, so no enrich call is needed.
"""
from dataclasses import replace

from ..models import Company, Posting
from .base import get_json, strip_html, to_dt

API = ("https://{host}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
       "?onlyData=true&expand=requisitionList.secondaryLocations"
       "&finder=findReqs;siteNumber={site},limit={limit},sortBy=POSTING_DATES_DESC")
# The list call returns no body text; the detail call does. Fetched only for survivors
# (see ENRICHERS in sources.py), exactly like Workday.
DETAIL = ("https://{host}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
          "?onlyData=true&expand=all&finder=ById;Id={jid},siteNumber={site}")
JOB_URL = "https://{host}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{site}/job/{jid}"


def parse(host: str, site: str, payload: dict) -> list[Posting]:
    items = (payload or {}).get("items") or []
    reqs = (items[0].get("requisitionList") if items else None) or []
    out = []
    for j in reqs:
        jid = j.get("Id")
        if jid is None:
            continue
        # Body is split across a few fields; join whichever are present.
        desc = " ".join(x for x in (j.get("ShortDescriptionStr"),
                                    j.get("ExternalResponsibilitiesStr"),
                                    j.get("ExternalQualificationsStr")) if x)
        out.append(Posting(
            uid=f"oracle:{host}:{jid}",
            ats="oracle",
            company=host,
            title=j.get("Title", "") or "",
            location=j.get("PrimaryLocation", "") or "",
            url=JOB_URL.format(host=host, site=site, jid=jid),
            posted_at=to_dt(j.get("PostedDate")),
            description=strip_html(desc),
            raw=j,
        ))
    return out


async def fetch(client, company: Company, *, limit=50) -> list[Posting]:
    if not company.wd_site:
        return []  # misconfigured oracle entry: needs the site number (stored in wd_site)
    host, site = company.slug, company.wd_site
    payload = await get_json(client, API.format(host=host, site=site, limit=limit))
    return parse(host, site, payload)


async def enrich(client, posting: Posting, company: Company) -> Posting:
    """Fetch the full job body for a single posting (second call). No-op if it fails."""
    jid = posting.raw.get("Id")
    if jid is None or not company.wd_site:
        return posting
    url = DETAIL.format(host=company.slug, site=company.wd_site, jid=jid)
    try:
        data = await get_json(client, url)
    except Exception:
        return posting
    it = (data.get("items") or [{}])[0]
    desc = " ".join(x for x in (it.get("ExternalDescriptionStr"),
                                it.get("ExternalResponsibilitiesStr"),
                                it.get("ExternalQualificationsStr")) if x)
    desc = strip_html(desc)
    return replace(posting, description=desc) if desc else posting
