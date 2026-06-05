from datetime import datetime, timezone

from .models import Company, Posting, Profile, Score, Urgency


def _is_fresh(posting: Posting, profile: Profile, now: datetime) -> bool:
    if posting.posted_at is None:
        return False
    return (now - posting.posted_at).total_seconds() <= profile.high_fresh_hours * 3600


def classify(posting: Posting, score: Score, company: Company | None,
             profile: Profile, now: datetime | None = None) -> Urgency | None:
    """Return urgency level, or None to drop. Assumes posting already passed rules."""
    now = now or datetime.now(timezone.utc)

    # Dream company overrides the score threshold (it passed the rules filter already).
    if company is not None and company.tier == "dream":
        return Urgency.HIGH

    if score.value >= profile.ping_threshold:
        if _is_fresh(posting, profile, now) and score.value >= profile.high_score:
            return Urgency.HIGH
        return Urgency.MEDIUM

    if score.value >= profile.digest_threshold:
        return Urgency.LOW

    return None
