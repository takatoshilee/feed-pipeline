"""Remembers which postings the LLM has already scored, so the daily sweep spends its
budget only on roles it has never looked at. Maps uid -> {"fit": int, "at": iso date}.
Persisted as JSON next to the seen-set (restored and saved by the sweep workflow)."""
import json
import os
from datetime import datetime, timedelta, timezone


# Bump when the scorer or the profile changes enough that old scores should be redone.
# Entries from another version count as never scored, so the sweep re-scores them over the
# next days (SWEEP_CAP a day). v2 (Nov 2026): separate-answers scoring, wider target lanes.
SCORER_VERSION = 2


class ScoreCache:
    def __init__(self, path: str):
        self.path = path
        self._d: dict[str, dict] = {}

    def load(self) -> "ScoreCache":
        if os.path.exists(self.path):
            try:
                with open(self.path) as f:
                    self._d = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._d = {}
        return self

    def __contains__(self, uid: str) -> bool:
        v = self._d.get(uid)
        return bool(v) and v.get("v") == SCORER_VERSION

    def __len__(self) -> int:
        return len(self._d)

    def fit(self, uid: str):
        v = self._d.get(uid)
        return v["fit"] if v and v.get("v") == SCORER_VERSION else None

    def put(self, uid: str, fit: int, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._d[uid] = {"fit": int(fit), "at": now.date().isoformat(), "v": SCORER_VERSION}

    def save(self, keep_days: int = 400, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=keep_days)).date().isoformat()
        self._d = {k: v for k, v in self._d.items() if v.get("at", "") >= cutoff}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._d, f)
        os.replace(tmp, self.path)
