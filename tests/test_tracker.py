from datetime import date

from job_radar.tracker import (due_soon, unapplied_strong, top_unapplied, stats, parse_date,
                              priority_rank, has_priority, must_apply, pending_count, last_active,
                              recently_posted)

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


def test_priority_rank_and_has_priority():
    assert priority_rank(row(Priority="must")) == 0
    assert priority_rank(row(Priority="High")) == 1   # case-insensitive
    assert priority_rank(row(Priority="2")) == 2
    assert priority_rank(row(Priority="")) == 9        # blank sorts last
    assert priority_rank(row(Priority="banana")) == 9  # unknown sorts last
    assert has_priority(row(Priority="urgent")) and not has_priority(row(Priority="low"))


def test_must_apply_only_flagged_pending_sorted_by_priority_then_fit():
    rows = [
        row(uid="a", Priority="high", Fit="70"),
        row(uid="b", Priority="must", Fit="60"),               # must outranks high
        row(uid="c", Priority="high", Fit="90"),               # higher fit within 'high'
        row(uid="d", Priority="must", Fit="80", Status="Applied"),  # applied -> excluded
        row(uid="e", Priority="low", Fit="99"),                # not flagged -> excluded
    ]
    assert [r["uid"] for r in must_apply(rows)] == ["b", "c", "a"]


def test_recently_posted_only_fresh_pending_newest_first():
    rows = [
        row(uid="a", Posted="2026-06-09"),                       # 1 day ago -> fresh
        row(uid="b", Posted="2026-06-04"),                       # 6 days ago -> fresh
        row(uid="c", Posted="2026-05-20"),                       # 21 days -> too old
        row(uid="d", Posted="2026-06-08", Status="Applied"),     # fresh but applied -> excluded
        row(uid="e", Posted=""),                                 # unknown date -> skipped
    ]
    res = recently_posted(rows, TODAY, within_days=7)
    assert [r["uid"] for r in res] == ["a", "b"]   # newest first, only fresh + pending


def test_pending_count_and_last_active():
    rows = [
        row(uid="a"),
        row(uid="b", Status="Applied", **{"Applied on": "2026-06-05"}),
        row(uid="c", Status="Applied", **{"Applied on": "2026-06-08"}),
    ]
    assert pending_count(rows) == 1
    assert last_active(rows) == date(2026, 6, 8)   # most recent applied-on
    assert last_active([row(uid="x")]) is None     # nothing applied yet
