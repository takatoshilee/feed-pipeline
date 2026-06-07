"""Google Sheet as the tracker's source of truth. The gspread/google-auth imports are
lazy (inside connect) so the row logic below is testable with a fake worksheet."""
from .models import Posting, Score

HEADERS = ["uid", "Company", "Role", "Link", "Fit", "Location", "Posted",
           "Status", "Deadline", "Priority", "Notes", "Applied on"]


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
    return ws


def ensure_headers(ws) -> None:
    """Make row 1 the expected header. Already correct -> nothing. Empty -> write it.
    Wrong but with no data beneath (e.g. a stray value pasted into A1) -> overwrite it.
    Wrong but with data already under it -> leave alone, to avoid shifting columns."""
    current = ws.row_values(1)
    if current == HEADERS:
        return
    if not current:
        ws.append_row(HEADERS)
        return
    if len(ws.col_values(1)) <= 1:  # row 1 holds something, but no data rows follow
        for i, h in enumerate(HEADERS, start=1):
            ws.update_cell(1, i, h)


def existing_uids(ws) -> set:
    return set(ws.col_values(1)[1:])  # column 1 minus the header


def append_match(ws, posting: Posting, score: Score) -> None:
    posted = posting.posted_at.date().isoformat() if posting.posted_at else ""
    ws.append_row(
        [posting.uid, posting.company, posting.title, posting.url, score.value,
         posting.location, posted, "New", "", "", "", ""],
        value_input_option="USER_ENTERED",
    )


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


class SheetSink:
    """Mirrors new matches into the tracker Sheet during a poll. Snapshots the
    existing uids once at construction, so a single run never re-adds a row that
    is already there (and repeated uids within the run are added only once)."""

    def __init__(self, ws):
        self.ws = ws
        self._seen = existing_uids(ws)

    def add(self, posting: Posting, score: Score) -> bool:
        """Append the posting as a new 'New' row. Returns False (no-op) if its uid
        is already in the Sheet."""
        if posting.uid in self._seen:
            return False
        append_match(self.ws, posting, score)
        self._seen.add(posting.uid)
        return True
