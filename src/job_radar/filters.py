import re
from datetime import datetime, timezone

from .models import Posting, Profile

_LOC_SPLIT = re.compile(r"[,/()|\-]+")


def _location_blocked(loc: str, block_terms) -> bool:
    """A multi-word block term ('united kingdom') matches as a substring; a single-word
    term matches a whole comma/dash-split token, so 'india' blocks 'India - Remote' but
    NOT 'Indiana'. (Keep city names out of the block-list: see TUNING_RECOMMENDATIONS.md.)"""
    if not block_terms:
        return False
    tokens = {t.strip() for t in _LOC_SPLIT.split(loc) if t.strip()}
    for b in block_terms:
        if (b in loc) if " " in b else (b in tokens):
            return True
    return False


_CA_HINT = ("canada", "canadian", "ontario", "toronto", "waterloo", "montreal", "montréal",
            "vancouver", "ottawa", "quebec", "québec", "alberta", "calgary", "british columbia")
_US_HINT = ("united states", "usa", "u.s.", "san francisco", "new york", "seattle", "austin",
            "boston", "los angeles", "palo alto", "mountain view", "sunnyvale", "san jose",
            "san mateo", "santa clara", "menlo park", "bay area", "chicago", "denver", "atlanta",
            "bellevue", "redmond", "cupertino", "san diego", "pittsburgh", "raleigh", "dallas",
            "philadelphia", "washington", "phoenix", "nashville", "miami")


def visa_note(location: str) -> str:
    """A heads-up for the Sheet's Notes column: US ON-SITE roles need a sponsored J-1.
    Remote (work from Canada) and Canadian roles need nothing, so they return ''."""
    loc = (location or "").lower()
    if not loc or "remote" in loc:
        return ""
    if any(c in loc for c in _CA_HINT):
        return ""
    if any(u in loc for u in _US_HINT):
        return "US on-site: needs sponsored J-1 visa"
    return ""


def passes_rules(posting: Posting, profile: Profile, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    title = posting.title.lower()

    # Exclusions win outright.
    if any(x in title for x in profile.title_exclude):
        return False

    # Must match at least one include keyword (if any are configured).
    if profile.title_include and not any(x in title for x in profile.title_include):
        return False

    # Location: only evaluated when the posting has a location string.
    loc = (posting.location or "").lower().strip()
    if loc:
        if profile.locations_allow and not any(a in loc for a in profile.locations_allow):
            return False
        if _location_blocked(loc, profile.locations_block):
            return False

    # Freshness: only evaluated when posted_at is known.
    if posting.posted_at is not None:
        age_days = (now - posting.posted_at).total_seconds() / 86400
        if age_days > profile.freshness_days:
            return False

    return True
