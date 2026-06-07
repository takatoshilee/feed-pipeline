from datetime import date

from job_radar.tracker import due_soon, unapplied_strong, top_unapplied, stats, parse_date

TODAY = date(2026, 6, 10)


def row(**kw):
    base = {"uid": "u", "Company": "c", "Role": "r", "Fit": "50",
            "Status": "New", "Deadline": "", "Posted": "2026-06-01"}
    base.update(kw)
    return base


def test_parse_date_tolerates_formats():
    assert parse_date("2026-11-14") == date(2026, 11, 14)
    assert parse_date("Nov 14, 2026") == date(2026, 11, 14)
    assert parse_date("11/14/2026") == date(2026, 11, 14)
    assert parse_date("") is None
    assert parse_date("whenever") is None


def test_due_soon_only_pending_within_window():
    rows = [
        row(uid="a", Deadline="2026-06-12"),                    # 2 days -> due
        row(uid="b", Deadline="2026-06-20"),                    # 10 days -> no
        row(uid="c", Deadline="2026-06-12", Status="Applied"),  # already applied -> no
        row(uid="d", Deadline=""),                              # no deadline -> no
        row(uid="e", Deadline="2026-06-01"),                    # past -> no
    ]
    assert [r["uid"] for r in due_soon(rows, TODAY, within_days=3)] == ["a"]


def test_unapplied_strong_filters_fit_status_and_freshness():
    rows = [
        row(uid="a", Fit="90", Posted="2026-06-01"),            # strong, old, pending -> yes
        row(uid="b", Fit="70", Posted="2026-06-01"),            # too weak -> no
        row(uid="c", Fit="95", Posted="2026-06-09"),            # strong but 1 day old -> no
        row(uid="d", Fit="88", Status="Applied"),               # applied -> no
    ]
    res = unapplied_strong(rows, TODAY, min_fit=80, older_than_days=3)
    assert [r["uid"] for r in res] == ["a"]


def test_top_unapplied_excludes_applied_and_ranks_by_fit():
    rows = [row(uid="a", Fit="90"), row(uid="b", Fit="95", Status="Applied"), row(uid="c", Fit="80")]
    assert [r["uid"] for r in top_unapplied(rows, n=2)] == ["a", "c"]  # b applied -> excluded


def test_stats_counts_by_status():
    rows = [row(Fit="90"), row(Status="Applied"), row(Status="Applied"), row()]
    s = stats(rows)
    assert s["New"] == 2 and s["Applied"] == 2
