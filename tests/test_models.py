from datetime import datetime, timezone
from job_radar.models import Posting, Company, Score, Profile, Urgency


def test_posting_uid_is_identity_and_hashable():
    p1 = Posting(uid="greenhouse:stripe:1", ats="greenhouse", company="stripe",
                 title="SWE Intern", location="Toronto", url="http://x",
                 posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc), description="d", raw={"a": 1})
    p2 = Posting(uid="greenhouse:stripe:1", ats="greenhouse", company="stripe",
                 title="SWE Intern", location="Toronto", url="http://x",
                 posted_at=datetime(2026, 6, 1, tzinfo=timezone.utc), description="d", raw={"DIFFERENT": 2})
    assert p1 == p2  # raw excluded from equality
    assert len({p1, p2}) == 1


def test_company_defaults():
    c = Company(slug="stripe", ats="greenhouse")
    assert c.tier == "target"
    assert c.wd_host is None


def test_urgency_values():
    assert Urgency.HIGH.value == "high"
    assert {Urgency.HIGH, Urgency.MEDIUM, Urgency.LOW}


def test_profile_holds_thresholds():
    pr = Profile(summary="s", title_include=["intern"], title_exclude=["senior"],
                 locations_allow=["toronto"], locations_block=[], freshness_days=21)
    assert pr.ping_threshold == 65 and pr.high_score == 80
