"""Remembers which postings the LLM has already scored, so the daily sweep spends its
budget only on roles it has never looked at. Maps uid -> {"fit": int, "at": iso date}.
Persisted as JSON next to the seen-set (restored and saved by the sweep workflow)."""
import json
import os
from datetime import datetime, timedelta, timezone


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
        return uid in self._d

    def __len__(self) -> int:
        return len(self._d)

    def fit(self, uid: str):
        v = self._d.get(uid)
        return v["fit"] if v else None

    def put(self, uid: str, fit: int, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        self._d[uid] = {"fit": int(fit), "at": now.date().isoformat()}

    def save(self, keep_days: int = 120, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=keep_days)).date().isoformat()
        self._d = {k: v for k, v in self._d.items() if v.get("at", "") >= cutoff}
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._d, f)
        os.replace(tmp, self.path)
