from datetime import datetime, timezone

from job_radar.models import Posting, Score
from job_radar.sheet import (HEADERS, ensure_headers, append_match, set_status,
                             set_deadline, existing_uids, all_records)


class FakeWS:
    """Minimal in-memory stand-in for a gspread worksheet (rows = list of lists)."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []

    def row_values(self, n):
        return self.rows[n - 1] if 0 < n <= len(self.rows) else []

    def append_row(self, vals, value_input_option=None):
        self.rows.append([str(v) for v in vals])

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
