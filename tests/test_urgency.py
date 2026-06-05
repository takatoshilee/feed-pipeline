from datetime import datetime, timezone, timedelta

from job_radar.models import Posting, Score, Company, Profile, Urgency
from job_radar.urgency import classify

NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
PROFILE = Profile(summary="s", title_include=[], title_exclude=[], locations_allow=[],
                  locations_block=[], freshness_days=21, ping_threshold=65,
                  digest_threshold=50, high_score=80, high_fresh_hours=2)
TARGET = Company(slug="c", ats="greenhouse", tier="target")
DREAM = Company(slug="d", ats="greenhouse", tier="dream")


def _p(posted=NOW):
    return Posting(uid="x:1", ats="greenhouse", company="c", title="t", location="l",
                   url="u", posted_at=posted, description="d")


def test_dream_always_high():
    assert classify(_p(), Score(10, "r"), DREAM, PROFILE, NOW) == Urgency.HIGH


def test_high_when_fresh_and_high_score():
    fresh = NOW - timedelta(minutes=30)
    assert classify(_p(fresh), Score(85, "r"), TARGET, PROFILE, NOW) == Urgency.HIGH


def test_medium_when_relevant_not_fresh():
    old = NOW - timedelta(days=5)
    assert classify(_p(old), Score(85, "r"), TARGET, PROFILE, NOW) == Urgency.MEDIUM
    # fresh but score below high_score -> medium
    assert classify(_p(NOW), Score(70, "r"), TARGET, PROFILE, NOW) == Urgency.MEDIUM


def test_low_and_drop():
    assert classify(_p(), Score(55, "r"), TARGET, PROFILE, NOW) == Urgency.LOW
    assert classify(_p(), Score(40, "r"), TARGET, PROFILE, NOW) is None
