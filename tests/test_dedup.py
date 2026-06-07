from datetime import datetime, timezone, timedelta

from job_radar.models import Posting
from job_radar.dedup import SeenStore


def _posting(uid):
    return Posting(uid=uid, ats="greenhouse", company="c", title="t",
                   location="l", url="u", posted_at=None, description="d")


def test_new_then_seen_persists(tmp_path):
    path = str(tmp_path / "seen.json")
    store = SeenStore(path).load()
    p = _posting("greenhouse:c:1")
    assert store.is_new(p)
    store.mark(p)
    store.save()

    reloaded = SeenStore(path).load()
    assert not reloaded.is_new(p)


def test_save_prunes_old_entries(tmp_path):
    path = str(tmp_path / "seen.json")
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store = SeenStore(path).load()
    old = _posting("old:1")
    fresh = _posting("fresh:1")
    store.mark(old, now=now - timedelta(days=90))
    store.mark(fresh, now=now)
    store.save(keep_days=60, now=now)

    reloaded = SeenStore(path).load()
    assert reloaded.is_new(old)        # pruned
    assert not reloaded.is_new(fresh)  # kept


def test_remark_refreshes_timestamp_so_live_postings_survive_prune(tmp_path):
    path = str(tmp_path / "seen.json")
    now = datetime(2026, 6, 1, tzinfo=timezone.utc)
    store = SeenStore(path).load()
    p = _posting("x:1")
    store.mark(p, now=now - timedelta(days=90))  # first seen long ago
    store.mark(p, now=now)                        # observed again now -> refresh
    store.save(keep_days=60, now=now)

    reloaded = SeenStore(path).load()
    assert not reloaded.is_new(p)  # NOT pruned: last-seen is recent
