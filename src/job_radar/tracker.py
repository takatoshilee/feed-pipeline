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


def status(row) -> str:
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


def stats(rows) -> dict:
    """Count rows by status."""
    counts: dict = {}
    for r in rows:
        counts[status(r)] = counts.get(status(r), 0) + 1
    return counts
