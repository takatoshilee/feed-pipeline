"""Nov 2026 changes: whole-word title rules, any-country ranking, separate-answer scoring,
keyword-backup scores kept out of the Sheet, and in-place re-scoring of open rows."""
from datetime import date, datetime, timezone

from job_radar import topn
from job_radar.filters import has_word, location_tier, passes_rules
from job_radar.models import Posting, Profile, Score
from job_radar.scorecache import ScoreCache
from job_radar.scorer import _coerce_score, compute_fit, heuristic_score, is_ai_score
from job_radar.sheet import HEADERS, rescore_updates

PROFILE = Profile(summary="s", title_include=["intern", "co-op", "data", "engineer"],
                  title_exclude=["sales", "lead", "architect", " ii", "sr."],
                  locations_allow=["toronto", "canada", "remote"], locations_block=["india"],
                  freshness_days=365, locations_mode="rank")


def P(title, loc="Toronto, ON"):
    return Posting(uid="g:c:1", ats="greenhouse", company="c", title=title, location=loc,
                   url="u", posted_at=None, description="")


def test_title_rules_match_whole_words_only():
    assert passes_rules(P("Salesforce Developer Intern"), PROFILE)
    assert passes_rules(P("Technology Leadership Program Intern"), PROFILE)
    assert not passes_rules(P("Sales Intern"), PROFILE)
    assert not passes_rules(P("Tech Lead, Data"), PROFILE)
    assert not passes_rules(P("Software Engineer II"), PROFILE)
    assert not passes_rules(P("Sr. Data Engineer"), PROFILE)
    assert has_word("summer interns", ["intern"])        # plural still matches
    assert not has_word("internal tools", ["intern"])


def test_rank_mode_lets_any_country_through_for_student_roles_only():
    assert passes_rules(P("Data Intern", "London, UK"), PROFILE)
    assert passes_rules(P("Data Intern", "Bengaluru, India"), PROFILE)
    assert not passes_rules(P("Data Engineer", "Berlin, Germany"), PROFILE)   # full-time abroad
    assert passes_rules(P("Data Engineer", "Toronto, ON"), PROFILE)          # LLM gates this one
    assert location_tier("Toronto, ON", PROFILE) == "prefer"
    assert location_tier("Remote, India", PROFILE) == "other"                # blocked stays low
    assert location_tier("New York, NY", PROFILE) == "us"
    assert location_tier("Berlin, Germany", PROFILE) == "other"


def test_filter_mode_still_drops_other_countries():
    from dataclasses import replace
    old = replace(PROFILE, locations_mode="filter")
    assert not passes_rules(P("Data Intern", "London, UK"), old)


def test_fit_is_computed_from_separate_answers():
    assert compute_fit(True, "", 10, 10, False) == 95
    assert compute_fit(True, "", 5, 5, False) == 50
    assert compute_fit(True, "", 8, 8, True) == 62            # low-level loses 15
    assert compute_fit(False, "", 10, 10, False) == 30        # not a student role: capped
    assert compute_fit(True, "citizenship", 9, 9, False) == 30
    s = _coerce_score({"student_role": True, "blocker": "", "requirements_met": 7,
                       "interest": 9, "lane": "Data", "low_level": "false", "reason": "r",
                       "resume": "ai", "term": "Summer 2027"})
    assert s.value == 77 and s.lane == "data" and s.flags == [] and s.resume == "ai"
    s2 = _coerce_score({"student_role": True, "blocker": "grad_degree", "requirements_met": 9,
                        "interest": 9, "lane": "ml", "low_level": True})
    assert s2.value == 30 and "low-level" in s2.flags and "grad degree" in s2.flags


def test_keyword_backup_is_not_an_ai_score():
    h = heuristic_score(P("Software Engineer Intern"), PROFILE)
    assert h.ok and not is_ai_score(h)
    assert is_ai_score(Score(80, "r"))
    assert not is_ai_score(Score(0, "err", ok=False))


def row(**kw):
    base = {"uid": "x", "Company": "acme", "Role": "Software Engineer Intern", "Applied": "FALSE",
            "Fit": "70", "Resume": "SWE", "Term": "Summer 2027", "Why": "", "Link": "",
            "Location": "Toronto, ON", "Posted": "", "Status": "New", "Deadline": "",
            "Priority": "", "Lane": "", "Flags": ""}
    base.update(kw)
    return base


TODAY = date(2026, 11, 10)


def sc(**kw):
    return topn.score(row(**kw), TODAY)[0]


def test_top30_target_lanes_flags_places_and_age():
    assert sc(Lane="marketing") > sc(Lane="swe")
    assert sc(Role="Product Marketing Intern") > sc()            # title fallback, no Lane yet
    assert sc(Role="Data Analyst Intern") > sc()
    assert sc(Lane="ml", Flags="low-level") < sc(Lane="swe")      # judged from the posting
    assert sc() > sc(Location="Austin, TX") > sc(Location="London, UK")
    assert sc(Posted="2026-11-08") > sc(Posted="2026-10-20") > sc(Posted="2026-05-01")
    assert topn.eligible(row(Term="Winter 2027"))


def test_rescore_updates_only_touch_scorer_columns():
    header = HEADERS
    records = [row(uid="a", Priority="must", Notes="mine"), row(uid="b")]
    new = Score(83, "better", resume="ai", term="Fall 2027", lane="data", flags=["low-level"])
    ups = rescore_updates(records, header, {"a": new})
    cells = {u["range"]: u["values"][0][0] for u in ups}
    col = {h: chr(65 + i) for i, h in enumerate(header)}
    assert cells[f"{col['Fit']}2"] == 83 and cells[f"{col['Lane']}2"] == "data"
    assert cells[f"{col['Flags']}2"] == "low-level" and cells[f"{col['Resume']}2"] == "AI"
    assert not any(r.endswith("3") for r in cells)                 # row b untouched
    touched = {r.rstrip("0123456789") for r in cells}
    assert col["Priority"] not in touched and col["Notes"] not in touched


def test_old_cache_entries_count_as_unscored(tmp_path):
    import json
    path = tmp_path / "scored.json"
    path.write_text(json.dumps({"old": {"fit": 72, "at": "2026-11-01"}}))
    c = ScoreCache(str(path)).load()
    assert "old" not in c
    c.put("new", 80, datetime(2026, 11, 10, tzinfo=timezone.utc))
    assert "new" in c and c.fit("new") == 80
