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

    def is_new(self, posting: Posting) -> bool:
        return posting.uid not in self._seen

    def mark(self, posting: Posting, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._seen.setdefault(posting.uid, now.isoformat())

    def save(self, keep_days: int = 60, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(days=keep_days)
        pruned = {uid: ts for uid, ts in self._seen.items() if _parse(ts) >= cutoff}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(pruned, f)
        self._seen = pruned
