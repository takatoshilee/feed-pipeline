import json
import os
from datetime import datetime, timedelta, timezone

from .models import Posting


def _parse(ts: str) -> datetime:
    try:
        dt = datetime.fromisoformat(ts)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


class SeenStore:
    """Maps posting uid -> ISO timestamp first seen. Persisted as JSON."""

    def __init__(self, path: str):
        self.path = path
        self._seen: dict[str, str] = {}

    def load(self) -> "SeenStore":
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self._seen = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._seen = {}
        return self

    def is_empty(self) -> bool:
        return not self._seen

    def known_companies(self) -> set:
        """The (ats, slug) pairs the store has ever seen a posting from. A board absent
        here is newly added, so its backlog should be primed silently, not pinged.
        (uid is 'ats:slug:native_id'; native_id may contain colons, the first two don't.)"""
        out = set()
        for uid in self._seen:
            parts = uid.split(":", 2)
            if len(parts) >= 2:
                out.add((parts[0], parts[1]))
        return out

    def is_new(self, posting: Posting) -> bool:
        return posting.uid not in self._seen

    def mark(self, posting: Posting, now: datetime | None = None) -> None:
        # Store last-seen (overwrite, not setdefault): re-observing a still-listed
        # posting refreshes its timestamp so it never ages out of the store while live.
        now = now or datetime.now(timezone.utc)
        self._seen[posting.uid] = now.isoformat()

    def save(self, keep_days: int = 30, now: datetime | None = None) -> None:
        # keep_days bounds how long a CLOSED posting is remembered; live postings are
        # re-marked every run so they never age out. 30d is ample (guards re-ping of a
        # job that closes then re-opens) and roughly halves steady-state size.
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=keep_days)
        pruned = {uid: ts for uid, ts in self._seen.items() if _parse(ts) >= cutoff}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(pruned, f, separators=(",", ":"))  # compact: smaller cache uploads
        self._seen = pruned
