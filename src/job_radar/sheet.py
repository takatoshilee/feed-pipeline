"""Google Sheet as the tracker's source of truth. The gspread/google-auth imports are
lazy (inside connect) so the row logic below is testable with a fake worksheet."""
from datetime import date

from .filters import visa_note
from .models import Posting, Score

# Column order matches how Taka uses the tracker: Applied (the one-click checkbox) sits
# right after Role so he can tick a job the moment he reads it, then the "is this worth it
# and when" cluster (Fit / Resume / Term / Why), then logistics. _row_for_header and
# all_records read the LIVE header, so writes stay correct even if he drags columns around.
HEADERS = ["uid", "Company", "Role", "Applied", "Fit", "Resume", "Term", "Why", "Link",
           "Location", "Posted", "Status", "Deadline", "Priority", "Notes", "Applied on",
           "Added on"]


def _col(name: str) -> int:
    return HEADERS.index(name) + 1  # gspread columns are 1-based


def connect(creds_path: str, sheet_id: str):
    """Authorize via a service account and return the first worksheet (headers ensured)."""
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        creds_path, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    ws = gspread.authorize(creds).open_by_key(sheet_id).sheet1
    ensure_headers(ws)
    ensure_checkbox(ws)
    return ws


def ensure_checkbox(ws) -> None:
    """Make the 'Applied' column one-click checkboxes (boolean data validation) for the
    DATA rows only, so flagging a job applied is a single tick. Bounding to the data rows
    matters: validation on the unbounded column expands the sheet's used-range into phantom
    blank rows. Best-effort: skipped if unsupported (tests)."""
    header = ws.row_values(1) or HEADERS
    if "Applied" not in header:
        return
    col = header.index("Applied")   # 0-based for the API; from the ACTUAL header
    nrows = len(ws.col_values(1))   # includes the header row
    if nrows < 2:
        return                       # no data rows yet
    try:
        ws.spreadsheet.batch_update({"requests": [{
            "setDataValidation": {
                "range": {"sheetId": ws.id, "startRowIndex": 1, "endRowIndex": nrows,
                          "startColumnIndex": col, "endColumnIndex": col + 1},
                "rule": {"condition": {"type": "BOOLEAN"}, "showCustomUi": True},
            }
        }]})
    except Exception as e:
        print(f"sheet: could not set checkbox validation ({e!r})")


def _a1_col(n: int) -> str:
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def sort_rows(ws, today) -> int:
    """Reorder the data rows by 'when should I apply' (tracker.apply_sort_key), preserving
    every column including Taka's own edits. Writes columns in the sheet's ACTUAL header
    order (not HEADERS), so it stays correct even if he drags columns around. Returns the
    number of rows reordered."""
    from .tracker import apply_sort_key
    header = ws.row_values(1) or HEADERS
    records = all_records(ws)   # keyed by the actual header row
    if not records:
        return 0
    records.sort(key=lambda r: apply_sort_key(r, today))
    body = [[r.get(h, "") for h in header] for r in records]
    rng = f"A2:{_a1_col(len(header))}{len(body) + 1}"
    ws.update(range_name=rng, values=body, value_input_option="USER_ENTERED")
    return len(body)


def ensure_headers(ws) -> None:
    """Make row 1 the expected header. Already correct -> nothing. Empty -> write it.
    Wrong but with no data beneath (e.g. a stray value pasted into A1) -> overwrite it.
    A prefix of HEADERS with data beneath (we ADDED trailing columns like 'Added on') ->
    extend it in place without touching data. Any other populated header -> leave alone."""
    current = ws.row_values(1)
    if current == HEADERS:
        return
    if not current:
        ws.append_row(HEADERS)
        return
    if len(ws.col_values(1)) <= 1:  # row 1 holds something, but no data rows follow
        for i, h in enumerate(HEADERS, start=1):
            ws.update_cell(1, i, h)
        return
    # Has data. If we only appended new trailing columns, extend the header to match.
    if len(current) < len(HEADERS) and HEADERS[:len(current)] == current:
        for i in range(len(current) + 1, len(HEADERS) + 1):
            ws.update_cell(1, i, HEADERS[i - 1])


def existing_uids(ws) -> set:
    return set(ws.col_values(1)[1:])  # column 1 minus the header


def _row_dict(posting: Posting, score: Score, added_on: str = "") -> dict:
    posted = posting.posted_at.date().isoformat() if posting.posted_at else ""
    return {"uid": posting.uid, "Company": posting.company, "Role": posting.title,
            "Fit": score.value, "Resume": score.resume.upper(), "Term": score.term,
            "Why": score.reason, "Link": posting.url, "Location": posting.location,
            "Posted": posted, "Status": "New", "Deadline": "", "Priority": "",
            "Notes": visa_note(posting.location), "Applied on": "", "Added on": added_on,
            "Applied": "FALSE"}


def _row_for_header(posting: Posting, score: Score, added_on: str, header: list) -> list:
    """Row values laid out in the SHEET's actual column order, so writes stay correct even
    if Taka drags columns around (e.g. moves the Applied checkbox next to Role)."""
    d = _row_dict(posting, score, added_on)
    return [d.get(h, "") for h in header]


def _row_values(posting: Posting, score: Score, added_on: str = "") -> list:
    d = _row_dict(posting, score, added_on)
    return [d[h] for h in HEADERS]


def append_match(ws, posting: Posting, score: Score) -> None:
    header = ws.row_values(1) or HEADERS
    ws.append_row(_row_for_header(posting, score, "", header), value_input_option="USER_ENTERED")


def _row_for_uid(ws, uid: str):
    col = ws.col_values(1)
    return col.index(uid) + 1 if uid in col else None  # 1-based row, or None


def set_status(ws, uid: str, status: str, applied_on: str = "") -> bool:
    r = _row_for_uid(ws, uid)
    if r is None:
        return False
    ws.update_cell(r, _col("Status"), status)
    if applied_on:
        ws.update_cell(r, _col("Applied on"), applied_on)
    return True


def set_deadline(ws, uid: str, deadline: str) -> bool:
    r = _row_for_uid(ws, uid)
    if r is None:
        return False
    ws.update_cell(r, _col("Deadline"), deadline)
    return True


def all_records(ws) -> list:
    return ws.get_all_records()


def mark_closed(ws, open_uids: set, ok_board_slugs: set) -> int:
    """Flag tracked roles that have DISAPPEARED from a board we polled OK this run as
    Status='Closed', so Taka doesn't waste time on a dead link when he sits down to apply.

    Conservative on purpose (false 'Closed' is worse than a missed one):
    - only touches a role whose board (the slug in its uid: 'ats:slug:nativeid') is in
      ok_board_slugs, i.e. that board actually fetched successfully this run. A transient
      board error -> we skip its roles rather than wrongly close them.
    - never overrides a row Taka has acted on (Applied checkbox, or Status Applied/Closed/Skip).
    Returns the number newly marked Closed. Header-aware (writes the real Status column)."""
    header = ws.row_values(1) or HEADERS
    if "Status" not in header or "uid" not in header:
        return 0
    scol = header.index("Status")
    updates = []
    for i, r in enumerate(ws.get_all_records(), start=2):   # row 2 = first data row
        uid = str(r.get("uid", "")).strip()
        if not uid or ":" not in uid:
            continue
        applied = str(r.get("Applied", "")).strip().upper() in ("TRUE", "✓", "YES", "1")
        cur = (str(r.get("Status", "")).strip() or "New")
        if applied or cur in ("Applied", "Closed", "Skip"):
            continue
        slug = uid.split(":")[1]
        if slug in ok_board_slugs and uid not in open_uids:
            updates.append({"range": f"{_a1_col(scol + 1)}{i}", "values": [["Closed"]]})
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    return len(updates)


def stamp_applied(ws, today: str | None = None) -> int:
    """Auto-fill 'Applied on' for rows whose Applied checkbox is ticked but whose date
    cell is still empty, so ticking the box is the ONLY thing Taka has to do; the next
    poll (<=15 min later) records when. Never overwrites an existing date, so the stamp
    stays the FIRST day the tick was seen. Header-aware; returns rows stamped."""
    header = ws.row_values(1) or HEADERS
    if "Applied" not in header or "Applied on" not in header:
        return 0
    dcol = header.index("Applied on")
    today = today or date.today().isoformat()
    updates = []
    for i, r in enumerate(ws.get_all_records(), start=2):   # row 2 = first data row
        ticked = str(r.get("Applied", "")).strip().upper() in ("TRUE", "✓", "YES", "1")
        if ticked and not str(r.get("Applied on", "")).strip():
            updates.append({"range": f"{_a1_col(dcol + 1)}{i}", "values": [[today]]})
    if updates:
        ws.batch_update(updates, value_input_option="USER_ENTERED")
    return len(updates)


class SheetSink:
    """Buffers new matches and writes them to the tracker Sheet in one batched call on
    flush(). Snapshots existing uids at construction so a run never re-adds a row that
    is already there (and dedups repeats within the run). Batching matters: the Sheets
    API caps writes at ~60/minute, so one append_rows beats dozens of append_row calls."""

    def __init__(self, ws, added_on: str | None = None):
        self.ws = ws
        self._header = ws.row_values(1) or list(HEADERS)   # write in the sheet's real order
        self._seen = existing_uids(ws)
        self._buffer: list[list] = []
        self._added_on = added_on or date.today().isoformat()  # stamps when WE found the role

    def is_tracked(self, uid: str) -> bool:
        """True if this uid is already in the Sheet (so a re-run can skip scoring it)."""
        return uid in self._seen

    def add(self, posting: Posting, score: Score) -> bool:
        """Queue the posting as a new 'New' row (written on flush). Returns False if its
        uid is already in the Sheet or already queued this run."""
        if posting.uid in self._seen:
            return False
        self._buffer.append(_row_for_header(posting, score, self._added_on, self._header))
        self._seen.add(posting.uid)
        return True

    def flush(self) -> int:
        """Write all queued rows in a single batched call. Returns the number written.
        insert_data_option=INSERT_ROWS makes the append INSERT new rows rather than
        OVERWRITE (the API default), so a concurrent writer (e.g. a cron run firing while
        a manual backfill runs) can't clobber rows instead of adding them."""
        if not self._buffer:
            return 0
        rows, self._buffer = self._buffer, []
        self.ws.append_rows(rows, value_input_option="USER_ENTERED",
                            insert_data_option="INSERT_ROWS")
        return len(rows)
