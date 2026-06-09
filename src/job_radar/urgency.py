from datetime import datetime, timezone

from .models import Company, Posting, Profile, Score, Urgency


def _is_fresh(posting: Posting, profile: Profile, now: datetime) -> bool:
    if posting.posted_at is None:
        return False
    return (now - posting.posted_at).total_seconds() <= profile.high_fresh_hours * 3600


def classify(posting: Posting, score: Score, company: Company | None,
             profile: Profile, now: datetime | None = None) -> Urgency | None:
    """Return Discord urgency level, or None to not notify. Purely score-based now: a high
    LLM fit is the signal, even at a 'dream' company (a top company posting a role that's a
    bad fit for Taka shouldn't force a ping; it still lands in the Sheet). `company` is kept
    for signature stability but no longer overrides the score."""
    now = now or datetime.now(timezone.utc)

    if score.value >= profile.ping_threshold:
        if _is_fresh(posting, profile, now) and score.value >= profile.high_score:
            return Urgency.HIGH
        return Urgency.MEDIUM

    if score.value >= profile.digest_threshold:
        return Urgency.LOW

    return None
