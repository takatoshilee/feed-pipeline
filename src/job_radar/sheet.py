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
    if ws.row_values(1) != HEADERS:
        if not ws.row_values(1):
            ws.append_row(HEADERS)
        # If the header row exists but differs, leave it (Taka may have customized).


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
