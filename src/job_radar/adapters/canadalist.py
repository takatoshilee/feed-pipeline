"""Hand-curated Canadian internship list on GitHub (negarprh/Canadian-Tech-Internships-2027).

The list is a Markdown table in the README, not JSON, so this parses the table rows:
Company | Role | Location | Apply | Date Posted. A "↳" in the Company cell means "same
company as the row above". Rows whose Apply cell has no link (it says "Closed🔒") are
skipped. Like 'simplify', the whole list is one pseudo-company (slug 'canadalist'), so the
per-company silent prime absorbs the backlog on first add; the real company name goes in
the title.
"""
import hashlib
import re
from datetime import datetime, timezone

from dateutil import parser as dateparser

from ..models import Company, Posting
from .base import TIMEOUT, USER_AGENT

FEED = ("https://raw.githubusercontent.com/negarprh/"
        "Canadian-Tech-Internships-2027/main/README.md")

# The Apply cell is a shields.io badge wrapped in a link: [![Apply](badge)](URL)
_LINK = re.compile(r"\]\((https?://[^)\s]+)\)\s*$")


def _apply_url(cell: str) -> str | None:
    cell = cell.strip()
    m = _LINK.search(cell)
    return m.group(1) if m else None


def _date(s: str) -> datetime | None:
    """'Oct 19, 2026' -> aware datetime. Bad or empty cells -> None."""
    try:
        return dateparser.parse(s).replace(tzinfo=timezone.utc) if s else None
    except (ValueError, TypeError, OverflowError):
        return None


def _uid(url: str) -> str:
    return "canadalist:canadalist:" + hashlib.sha1(url.encode()).hexdigest()[:16]


def parse(text: str) -> list[Posting]:
    out = []
    company = ""
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        name, role, location, apply_cell, posted = cells[:5]
        if name.lower() == "company" or set(name) <= set("-: "):
            continue                                   # header or separator row
        if name != "↳":
            company = name
        url = _apply_url(apply_cell)
        if not url or not company:
            continue                                   # closed, or no company yet
        out.append(Posting(
            uid=_uid(url),
            ats="canadalist",
            company="canadalist",                      # == slug; real name in the title
            title=f"{company}: {role}",
            location=location,
            url=url,
            posted_at=_date(posted),
            description="",
            raw={"company": company, "role": role},
        ))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    resp = await client.get(FEED, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    return parse(resp.text)
