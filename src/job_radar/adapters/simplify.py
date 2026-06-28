"""SimplifyJobs feed: github.com/SimplifyJobs/Summer2026-Internships.

One bulk source (~16k listings, ~1.3k active) spanning many companies, including
ones not on our per-ATS watch-list. Modelled as a single pseudo-company (slug
'simplify') so pipeline.run's per-company silent-prime absorbs its whole backlog on
first add instead of flooding the channel. That prime keys on the uid's first two
segments (via SeenStore.known_companies) AND on (p.ats, p.company), so EVERY posting
here uses uid 'simplify:simplify:<id>' and company='simplify'. The real company name
lives in the title so Discord/Sheet still show it.

Feed-specific filtering happens here (active/visible, sponsorship, degree level); the
shared pipeline still applies the location + title rules and the LLM fit score.
"""
from datetime import datetime, timezone

from ..models import Company, Posting
from .base import get_json

FEED = ("https://raw.githubusercontent.com/SimplifyJobs/"
        "Summer2026-Internships/dev/.github/scripts/listings.json")

# Degree tokens a 2nd/3rd-year bachelor's student can't realistically meet.
_GRAD_ONLY = ("phd", "ph.d", "master", "mba", "doctor")
# Sponsorship values that are hard blockers for an international student.
_BLOCK_SPONSORSHIP = {"U.S. Citizenship is Required"}


def _ts(value) -> datetime | None:
    """SimplifyJobs dates are unix SECONDS (base.from_ms is milliseconds, wrong here)."""
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def _grad_only(degrees) -> bool:
    """True only if EVERY listed degree is graduate-level (bachelor's ineligible).
    Empty/unknown degrees -> False, let the title rules + scorer decide."""
    degs = [str(d).lower() for d in (degrees or [])]
    if not degs:
        return False
    return all(any(tok in d for tok in _GRAD_ONLY) for d in degs)


def parse(payload) -> list[Posting]:
    out = []
    for j in payload or []:
        if not j.get("active") or not j.get("is_visible", True):
            continue                                   # filled or hidden
        if str(j.get("sponsorship")) in _BLOCK_SPONSORSHIP:
            continue                                   # needs US citizenship
        if _grad_only(j.get("degrees")):
            continue                                   # PhD/Masters/MBA-only
        jid = j.get("id")
        if not jid:
            continue
        name = (j.get("company_name") or "").strip()
        title = (j.get("title") or "").strip()
        locs = j.get("locations") or []
        out.append(Posting(
            uid=f"simplify:simplify:{jid}",            # slug segment 'simplify' => prime works
            ats="simplify",
            company="simplify",                        # == slug; real name goes in the title
            title=f"{name}: {title}" if name else title,
            location=" | ".join(locs) if isinstance(locs, list) else str(locs),
            url=j.get("url") or "",
            posted_at=_ts(j.get("date_posted")),
            description="",                             # feed carries no description
            raw=j,
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    payload = await get_json(client, FEED)
    return parse(payload)
