from job_radar.__main__ import _parse_args


def test_defaults():
    a = _parse_args([])
    assert a.profile.endswith("profile.yaml")
    assert a.companies.endswith("companies.yaml")
    assert a.dry_run is False
    assert a.prime is False
    assert a.preview is False
    assert a.backfill is False
    assert a.limit is None
    assert a.company is None
    assert a.state is None


def test_overrides():
    a = _parse_args(["--dry-run", "--prime", "--limit", "5",
                     "--company", "stripe", "--state", "/tmp/s.json",
                     "--profile", "p.yaml", "--companies", "c.yaml"])
    assert a.dry_run is True
    assert a.prime is True
    assert a.limit == 5
    assert a.company == "stripe"
    assert a.state == "/tmp/s.json"
    assert a.profile == "p.yaml"
    assert a.companies == "c.yaml"
