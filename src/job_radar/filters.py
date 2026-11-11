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


# Title words that mark a student role. Outside the preferred locations (locations_mode
# "rank") only these get scored, so the LLM isn't spent on every full-time job worldwide.
STUDENT_TERMS = ("intern", "internship", "co-op", "co op", "coop", "student", "new grad",
                 "new-grad", "werkstudent", "trainee", "apprentice", "placement", "summer analyst")


def _word_re(term: str):
    """Match a config term as whole word(s): 'sales' hits 'Sales Intern' but not
    'Salesforce', 'lead' hits 'Tech Lead' but not 'Leadership'. A trailing 's' is allowed
    so 'intern' still matches 'Interns'. Padding spaces in old config (' ml ') are ignored."""
    t = re.escape(term.strip())
    return re.compile(rf"(?<![a-z0-9]){t}s?(?![a-z0-9])")


_WORD_CACHE: dict = {}


def has_word(text: str, terms) -> bool:
    for term in terms:
        if not term.strip():
            continue
        rx = _WORD_CACHE.get(term)
        if rx is None:
            rx = _WORD_CACHE[term] = _word_re(term)
        if rx.search(text):
            return True
    return False


def location_tier(location: str, profile: Profile) -> str:
    """'prefer' (Canada / remote / no location), 'us', or 'other'. Used to rank and to
    decide pings; it never drops anything by itself."""
    loc = (location or "").lower().strip()
    if not loc or not profile.locations_allow or any(a in loc for a in profile.locations_allow):
        if not _location_blocked(loc, profile.locations_block):
            return "prefer"
    if any(u in loc for u in _US_HINT) or _location_blocked(loc, ("us", "usa")):
        return "us"
    return "other"


def passes_rules(posting: Posting, profile: Profile, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    title = posting.title.lower()

    # Exclusions win outright. Whole words only (see _word_re).
    if has_word(title, profile.title_exclude):
        return False

    # Must match at least one include keyword (if any are configured).
    if profile.title_include and not has_word(title, profile.title_include):
        return False

    # Location: only evaluated when the posting has a location string.
    loc = (posting.location or "").lower().strip()
    if loc:
        if profile.locations_mode == "rank":
            # Any country is allowed, ranked lower later. Outside the preferred places only
            # student roles go on to scoring.
            if location_tier(loc, profile) != "prefer" and not has_word(title, STUDENT_TERMS):
                return False
        else:
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
