import os
from job_radar.config import load_profile, load_companies, load_settings


def test_load_profile_lowercases_keywords(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text(
        "summary: hi\n"
        "title_include: [Intern, SWE]\n"
        "title_exclude: [Senior]\n"
        "locations_allow: [Toronto]\n"
        "locations_block: []\n"
        "freshness_days: 10\n"
    )
    pr = load_profile(str(f))
    assert pr.summary == "hi"
    assert pr.title_include == ["intern", "swe"]
    assert pr.title_exclude == ["senior"]
    assert pr.locations_allow == ["toronto"]
    assert pr.freshness_days == 10
    assert pr.ping_threshold == 65  # default


def test_load_companies(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("companies:\n  - {slug: stripe, ats: greenhouse, tier: target}\n")
    companies = load_companies(str(f))
    assert len(companies) == 1
    assert companies[0].slug == "stripe" and companies[0].ats == "greenhouse"


def test_load_settings_dry_run_when_no_webhook(monkeypatch):
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    s = load_settings()
    assert s.dry_run is True
    assert s.llm_model == "gemini-2.0-flash"


def test_load_settings_live_when_webhook_present(monkeypatch):
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "http://hook")
    monkeypatch.delenv("DRY_RUN", raising=False)
    s = load_settings()
    assert s.dry_run is False
    assert s.webhook_url == "http://hook"
