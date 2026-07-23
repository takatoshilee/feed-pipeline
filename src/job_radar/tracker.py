"""Pure logic over tracker rows (dicts keyed by the Sheet's header names). No I/O,
so it's fully unit-testable. A "row" looks like:
    {"uid": ..., "Company": ..., "Role": ..., "Fit": "85", "Status": "New",
     "Deadline": "2026-11-14", "Posted": "2026-06-05", ...}
"""
from datetime import date, datetime

# Statuses that mean "Taka hasn't applied yet", so deadline/nudge reminders still apply.
PENDING = "New"


def fit(row) -> int:
    try:
        return int(float(str(row.get("Fit", 0) or 0)))
    except (TypeError, ValueError):
        return 0


def _checked(v) -> bool:
    return str(v).strip().upper() in ("TRUE", "✓", "YES", "1")


def status(row) -> str:
    """Applied if the one-click 'Applied' checkbox is ticked (the easy path), else the
    text Status (for Skip), else New."""
    if _checked(row.get("Applied")):
        return "Applied"
    return (row.get("Status") or PENDING).strip() or PENDING


def parse_date(s):
    """Parse a human/ISO date string to a date, or None. Tolerant of common formats."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d %b %Y", "%b %d %Y",
                "%b %d, %Y", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def due_soon(rows, today: date, within_days: int = 3):
    """Pending rows whose deadline is today..+within_days, soonest first."""
    out = []
    for r in rows:
        if status(r) != PENDING:
            continue
        d = parse_date(r.get("Deadline"))
        if d is None:
            continue
        days = (d - today).days
        if 0 <= days <= within_days:
            out.append((days, r))
    out.sort(key=lambda x: x[0])
    return [r for _, r in out]


def unapplied_strong(rows, today: date, min_fit: int = 80, older_than_days: int = 3):
    """Pending, high-fit rows that have been sitting unapplied for a few days, best first."""
    out = []
    for r in rows:
        if status(r) != PENDING or fit(r) < min_fit:
            continue
        posted = parse_date(r.get("Posted"))
        if posted is not None and (today - posted).days < older_than_days:
            continue  # too fresh to nag about yet
        out.append(r)
    out.sort(key=fit, reverse=True)
    return out


def top_unapplied(rows, n: int = 5):
    """The n best pending (unapplied) rows by fit, for /top when time is short."""
    pending = [r for r in rows if status(r) == PENDING]
    pending.sort(key=fit, reverse=True)
    return pending[:n]


def apply_sort_key(row, today: date):
    """Sort key for 'when should I apply' (lower sorts first). Order of importance:
    (0) still actionable: Applied/Closed/Skip rows sink to the bottom so the top of the
    Sheet is always the live to-apply list, (1) the Priority flag Taka set (must/high first),
    (2) an urgent deadline (due within 14 days jumps up), (3) the actual deadline date,
    (4) best fit. With nothing filled in it falls back to fit, so the Sheet looks fit-sorted
    until he adds priorities/deadlines."""
    done = 0 if status(row) == PENDING else 1   # New=live (top); Applied/Closed/Skip=done (bottom)
    d = parse_date(row.get("Deadline"))
    dd = (d - today).days if d is not None else None
    urgent = 0 if (dd is not None and 0 <= dd <= 14) else 1
    deadline_order = dd if (dd is not None and dd >= 0) else 9999
    return (done, wrong_term(row), priority_rank(row), urgent, deadline_order, -fit(row))


# Work terms Taka cannot take (he recruits for Summer 2027 onwards; Winter 2027 is a
# class term). Rows whose Term names ONLY these sink below viable/undated ones.
_BAD_TERM_TOKENS = ("2024", "2025", "2026", "winter 2027")


def wrong_term(row) -> int:
    """1 if the posting's stated work term is pre-Summer-2027 (he can't take it), else 0.
    Undated / 'not stated' terms count as viable, the season is usually just unstated."""
    t = (row.get("Term") or "").strip().lower()
    if not t or "not stated" in t:
        return 0
    if ("2027" in t or "2028" in t) and "winter 2027" not in t:
        return 0
    return 1 if any(tok in t for tok in _BAD_TERM_TOKENS) else 0


def stats(rows) -> dict:
    """Count rows by status."""
    counts: dict = {}
    for r in rows:
        counts[status(r)] = counts.get(status(r), 0) + 1
    return counts


# Priority labels Taka can type in the Sheet's Priority column, most urgent -> least.
_PRIORITY = {
    "must": 0, "urgent": 0, "p0": 0, "asap": 0,
    "high": 1, "p1": 1, "1": 1, "h": 1,
    "medium": 2, "med": 2, "p2": 2, "2": 2, "m": 2,
    "low": 3, "p3": 3, "3": 3, "l": 3,
}


def priority_rank(row) -> int:
    """Lower = more urgent. Unrecognized or blank Priority sorts last (9)."""
    return _PRIORITY.get((row.get("Priority") or "").strip().lower(), 9)


def has_priority(row) -> bool:
    """True if Taka flagged this must/urgent/high (the 'apply no matter what' set)."""
    return priority_rank(row) <= 1


def must_apply(rows):
    """Pending rows flagged high priority, most-urgent then best-fit first. These should
    surface in reminders regardless of fit or age (Taka decided they matter)."""
    out = [r for r in rows if status(r) == PENDING and has_priority(r)]
    out.sort(key=lambda r: (priority_rank(r), -fit(r)))
    return out


def pending_count(rows) -> int:
    return sum(1 for r in rows if status(r) == PENDING)


def last_active(rows):
    """Most recent 'Applied on' date across the sheet, or None. A proxy for when Taka
    last triaged, used to frame the catch-up nudge after a busy stretch."""
    dates = [parse_date(r.get("Applied on")) for r in rows]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


def recently_posted(rows, today: date, within_days: int = 7):
    """Pending rows whose posting is within the last within_days, newest first. Rows
    without a known Posted date are skipped (no false positives)."""
    out = []
    for r in rows:
        if status(r) != PENDING:
            continue
        d = parse_date(r.get("Posted"))
        if d is not None and 0 <= (today - d).days <= within_days:
            out.append((d, r))
    out.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in out]


def recently_added(rows, today: date, within_days: int = 7):
    """Pending rows the radar ADDED in the last within_days (by the 'Added on' stamp),
    newest first. This is the truest 'new since I last looked' signal: it's when we found
    the role, not when it was posted. Rows without an Added-on date (pre-feature) are
    skipped, so the existing backlog isn't mislabelled as new."""
    out = []
    for r in rows:
        if status(r) != PENDING:
            continue
        d = parse_date(r.get("Added on"))
        if d is not None and 0 <= (today - d).days <= within_days:
            out.append((d, r))
    out.sort(key=lambda x: x[0], reverse=True)
    return [r for _, r in out]
