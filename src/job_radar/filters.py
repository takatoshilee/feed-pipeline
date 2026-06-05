from datetime import datetime, timezone

from .models import Posting, Profile


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
        if any(b in loc for b in profile.locations_block):
            return False

    # Freshness: only evaluated when posted_at is known.
    if posting.posted_at is not None:
        age_days = (now - posting.posted_at).total_seconds() / 86400
        if age_days > profile.freshness_days:
            return False

    return True
