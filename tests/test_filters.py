from datetime import datetime, timezone, timedelta

from job_radar.models import Posting, Profile
from job_radar.filters import passes_rules

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)
PROFILE = Profile(
    summary="s",
    title_include=["intern", "software"],
    title_exclude=["senior", " ii"],
    locations_allow=["toronto", "remote", "canada"],
    locations_block=["new york"],
    freshness_days=21,
)


def _p(title, location="Toronto", posted=NOW):
    return Posting(uid="x:1", ats="greenhouse", company="c", title=title,
                   location=location, url="u", posted_at=posted, description="d")


def test_include_required():
    assert passes_rules(_p("Software Engineer Intern"), PROFILE, NOW)
    assert not passes_rules(_p("Marketing Coordinator"), PROFILE, NOW)


def test_exclude_wins():
    assert not passes_rules(_p("Senior Software Engineer"), PROFILE, NOW)
    assert not passes_rules(_p("Software Engineer II"), PROFILE, NOW)


def test_location_allow_and_block():
    assert passes_rules(_p("Software Intern", location="Remote - Canada"), PROFILE, NOW)
    assert not passes_rules(_p("Software Intern", location="New York"), PROFILE, NOW)
    # empty location is not filtered out on location
    assert passes_rules(_p("Software Intern", location=""), PROFILE, NOW)


def test_freshness():
    stale = NOW - timedelta(days=40)
    assert not passes_rules(_p("Software Intern", posted=stale), PROFILE, NOW)
    # missing posted_at passes freshness (can't evaluate)
    assert passes_rules(_p("Software Intern", posted=None), PROFILE, NOW)


def test_location_block_is_token_aware():
    from job_radar.filters import _location_blocked
    assert _location_blocked("india - remote", ["india"])                 # foreign-remote blocked
    assert not _location_blocked("indianapolis, indiana", ["india"])      # NOT Indiana (US)
    assert not _location_blocked("london, ontario", ["india", "germany"]) # London ON safe
    assert _location_blocked("london, united kingdom", ["united kingdom"])  # phrase substring
    assert not _location_blocked("remote", [])                            # empty list = no-op
