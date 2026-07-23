from datetime import datetime, timezone

from job_radar.models import Posting, Score
from job_radar.sheet import (HEADERS, ensure_headers, append_match, set_status,
                             set_deadline, existing_uids, all_records, mark_closed, stamp_applied, SheetSink)


class FakeWS:
    """Minimal in-memory stand-in for a gspread worksheet (rows = list of lists)."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []

    def row_values(self, n):
        return self.rows[n - 1] if 0 < n <= len(self.rows) else []

    def append_row(self, vals, value_input_option=None):
        self.rows.append([str(v) for v in vals])

    def append_rows(self, rows, value_input_option=None, insert_data_option=None):
        for r in rows:
            self.rows.append([str(v) for v in r])

    def col_values(self, c):
        return [(r[c - 1] if c - 1 < len(r) else "") for r in self.rows]

    def update_cell(self, r, c, val):
        while len(self.rows) < r:
            self.rows.append([])
        row = self.rows[r - 1]
        while len(row) < c:
            row.append("")
        row[c - 1] = str(val)

    def get_all_records(self):
        if not self.rows:
            return []
        hdr = self.rows[0]
        return [dict(zip(hdr, [(r[i] if i < len(r) else "") for i in range(len(hdr))]))
                for r in self.rows[1:]]

    def batch_update(self, data, value_input_option=None):
        import re
        for item in data:
            rng = item["range"].split("!")[-1]
            m = re.match(r"([A-Z]+)(\d+)", rng)
            col = 0
            for ch in m.group(1):
                col = col * 26 + (ord(ch) - 64)
            row = int(m.group(2))
            self.update_cell(row, col, item["values"][0][0])


def _posting():
    return Posting(uid="greenhouse:stripe:1", ats="greenhouse", company="Stripe",
                   title="Software Engineer Intern", location="Toronto, ON", url="http://x",
                   posted_at=datetime(2026, 6, 5, tzinfo=timezone.utc), description="d")


def test_full_sheet_lifecycle():
    ws = FakeWS()
    ensure_headers(ws)
    assert ws.rows[0] == HEADERS

    append_match(ws, _posting(), Score(88, "great fit"))
    assert "greenhouse:stripe:1" in existing_uids(ws)
    rec = all_records(ws)[0]
    assert rec["Company"] == "Stripe" and rec["Status"] == "New"
    assert str(rec["Fit"]) == "88"

    assert set_status(ws, "greenhouse:stripe:1", "Applied", applied_on="2026-06-10")
    rec = all_records(ws)[0]
    assert rec["Status"] == "Applied" and rec["Applied on"] == "2026-06-10"

    assert set_deadline(ws, "greenhouse:stripe:1", "2026-11-14")
    assert all_records(ws)[0]["Deadline"] == "2026-11-14"

    assert not set_status(ws, "missing:uid", "Applied")  # unknown uid -> False, no crash


def test_ensure_headers_idempotent():
    ws = FakeWS([HEADERS])
    ensure_headers(ws)
    assert len(ws.rows) == 1  # didn't duplicate the header


def test_ensure_headers_overwrites_stray_a1():
    ws = FakeWS([["Discord Developer Portal"]])  # junk pasted into A1, no data beneath
    ensure_headers(ws)
    assert ws.rows[0] == HEADERS
    assert len(ws.rows) == 1


def test_ensure_headers_leaves_populated_custom_header():
    ws = FakeWS([["my", "own", "header"], ["a", "b", "c"]])  # real data under a custom header
    ensure_headers(ws)
    assert ws.rows[0] == ["my", "own", "header"]  # not clobbered


def test_ensure_headers_extends_when_trailing_columns_added():
    old = HEADERS[:-1]  # the pre-'Added on' 12-column header
    ws = FakeWS([old, ["greenhouse:x:1"] + [""] * (len(old) - 1)])  # one data row
    ensure_headers(ws)
    assert ws.rows[0] == HEADERS   # extended in place with 'Added on'
    assert len(ws.rows) == 2       # data row untouched


def test_sheet_sink_stamps_added_on():
    ws = FakeWS([HEADERS])
    sink = SheetSink(ws, added_on="2026-06-08")
    sink.add(_posting(), Score(80, "x"))
    sink.flush()
    assert all_records(ws)[0]["Added on"] == "2026-06-08"


def test_sheet_sink_writes_in_actual_header_order():
    # 'Applied' moved up next to Role; writes must follow the sheet's order, not HEADERS.
    reordered = ["uid", "Company", "Role", "Applied", "Link", "Fit", "Location", "Posted",
                 "Status", "Deadline", "Priority", "Notes", "Applied on", "Added on"]
    ws = FakeWS([reordered])
    sink = SheetSink(ws, added_on="2026-06-08")
    sink.add(_posting(), Score(88, "x"))
    sink.flush()
    rec = all_records(ws)[0]
    assert rec["Company"] == "Stripe"          # values landed under the right headers
    assert str(rec["Fit"]) == "88"
    assert rec["Applied"] == "FALSE" and rec["Role"] == "Software Engineer Intern"


def test_sheet_sink_dedups_against_existing_and_within_run():
    ws = FakeWS([HEADERS])
    append_match(ws, _posting(), Score(70, "old"))  # already in the sheet from a prior run
    sink = SheetSink(ws)

    assert sink.add(_posting(), Score(88, "again")) is False  # uid already present -> no-op

    fresh = Posting(uid="lever:cohere:9", ats="lever", company="Cohere", title="ML Intern",
                    location="Remote", url="http://y", posted_at=None, description="d")
    assert sink.add(fresh, Score(91, "new")) is True
    assert sink.add(fresh, Score(91, "dup")) is False  # same uid twice in one run -> once
    assert len(ws.rows) == 2  # still header + pre-existing row: nothing written until flush

    assert sink.flush() == 1   # one batched write
    assert sink.flush() == 0   # buffer drained, no-op
    assert existing_uids(ws) == {"greenhouse:stripe:1", "lever:cohere:9"}


def _track(ws, uid, ats, company):
    append_match(ws, Posting(uid=uid, ats=ats, company=company, title="SWE Intern",
                 location="Toronto", url="u", posted_at=None, description="d"), Score(80, "x"))


def test_mark_closed_flags_only_vanished_roles_on_polled_boards():
    ws = FakeWS([HEADERS])
    _track(ws, "greenhouse:stripe:1", "greenhouse", "stripe")   # still open
    _track(ws, "greenhouse:stripe:2", "greenhouse", "stripe")   # vanished from a polled board
    _track(ws, "lever:cohere:9", "lever", "cohere")             # vanished but board errored
    set_status(ws, "greenhouse:stripe:1", "Applied", applied_on="2026-06-10")  # already acted on

    # stripe fetched OK (only :1 still listed); cohere failed this run -> not in ok set.
    n = mark_closed(ws, open_uids={"greenhouse:stripe:1"}, ok_board_slugs={"stripe"})

    recs = {r["uid"]: r for r in all_records(ws)}
    assert n == 1
    assert recs["greenhouse:stripe:2"]["Status"] == "Closed"   # gone from a board we polled OK
    assert recs["greenhouse:stripe:1"]["Status"] == "Applied"  # acted-on row never overwritten
    assert recs["lever:cohere:9"]["Status"] == "New"           # board errored -> left alone

    assert mark_closed(ws, {"greenhouse:stripe:1"}, {"stripe"}) == 0  # idempotent: nothing new


def test_stamp_applied_dates_ticked_rows_once():
    hdr = ["uid", "Company", "Role", "Applied", "Applied on"]
    ws = FakeWS([hdr,
                 ["a:b:1", "X", "SWE Intern", "TRUE", ""],           # ticked, no date -> stamp
                 ["a:b:2", "Y", "ML Intern", "TRUE", "2026-07-01"],  # already dated -> untouched
                 ["a:b:3", "Z", "Data Intern", "", ""]])             # unticked -> untouched
    n = stamp_applied(ws, today="2026-07-23")
    assert n == 1
    recs = ws.get_all_records()
    assert recs[0]["Applied on"] == "2026-07-23"
    assert recs[1]["Applied on"] == "2026-07-01"   # first-seen date preserved
    assert recs[2]["Applied on"] == ""


def test_stamp_applied_missing_columns_is_noop():
    ws = FakeWS([["uid", "Company"], ["a:b:1", "X"]])
    assert stamp_applied(ws) == 0
