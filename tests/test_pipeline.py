from datetime import datetime, timezone

import pytest

from job_radar.config import Config, Settings
from job_radar.dedup import SeenStore
from job_radar.models import Company, Posting, Profile, Score, Urgency
from job_radar import pipeline
from job_radar.scorer import FakeProvider

NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)


class FakeNotifier:
    def __init__(self):
        self.ones = []
        self.digest = []

    async def send_one(self, posting, score, urgency, company, now):
        self.ones.append((posting, urgency))

    async def send_digest(self, items, now):
        self.digest = items


def _postings():
    return [
        Posting(uid="greenhouse:c:1", ats="greenhouse", company="c", title="Software Intern",
                location="Toronto", url="u1", posted_at=NOW, description="d"),
        Posting(uid="greenhouse:c:2", ats="greenhouse", company="c", title="Senior Engineer",
                location="Toronto", url="u2", posted_at=NOW, description="d"),  # excluded by rules
    ]


def _config(tmp_path):
    profile = Profile(summary="s", title_include=["intern"], title_exclude=["senior"],
                      locations_allow=["toronto"], locations_block=[], freshness_days=21)
    companies = [Company(slug="c", ats="greenhouse", tier="target")]
    settings = Settings(webhook_url=None, llm_api_key=None, llm_model="m", llm_provider="gemini",
                        role_id=None, seen_path=str(tmp_path / "seen.json"), dry_run=True)
    return Config(profile, companies, settings)


def _prime_seen(config):
    """Make the seen-set non-empty (without recording the real postings) so a run is
    treated as a normal run rather than a cold start."""
    seed = SeenStore(config.settings.seen_path)
    seed.mark(Posting(uid="seed:0", ats="x", company="x", title="t", location="l",
                      url="u", posted_at=None, description="d"), now=NOW)
    seed.save(now=NOW)


async def test_pipeline_primes_silently_on_cold_start(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)

    notifier = FakeNotifier()
    config = _config(tmp_path)
    stats = await pipeline.run(config, provider=FakeProvider(value=90), notifier=notifier, now=NOW)

    assert stats["primed"] == 2     # both postings recorded
    assert stats["pinged"] == 0     # but nothing fired
    assert notifier.ones == []


async def test_pipeline_filters_scores_routes(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)

    config = _config(tmp_path)
    _prime_seen(config)  # skip the cold-start path so pings actually fire

    notifier = FakeNotifier()
    stats = await pipeline.run(config, provider=FakeProvider(value=90, reason="r"),
                               notifier=notifier, now=NOW)

    assert stats["survivors"] == 1            # only the intern passes rules
    assert len(notifier.ones) == 1            # one ping
    assert notifier.ones[0][1] == Urgency.HIGH  # posted_at == now -> fresh, score 90 >= high_score
    # second run: nothing new (seen-set persisted)
    stats2 = await pipeline.run(config, provider=FakeProvider(value=90), notifier=FakeNotifier(), now=NOW)
    assert stats2["new"] == 0


async def test_force_prime_suppresses_pings_even_when_warm(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)

    config = _config(tmp_path)
    _prime_seen(config)  # seen-set is non-empty, so this is NOT a cold start

    notifier = FakeNotifier()
    stats = await pipeline.run(config, provider=FakeProvider(value=90), notifier=notifier,
                               now=NOW, force_prime=True)

    assert stats["pinged"] == 0      # forced prime suppresses notifications
    assert notifier.ones == []
    assert stats["primed"] >= 1      # postings still recorded


async def test_preview_is_read_only_and_ranks(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    config = _config(tmp_path)
    stats = await pipeline.preview(config, provider=FakeProvider(value=90, reason="r"))

    assert stats["survivors"] == 1   # only the intern passes rules
    assert stats["scored"] == 1
    import os
    assert not os.path.exists(config.settings.seen_path)  # read-only: no state written


class RaisingNotifier:
    async def send_one(self, *a):
        raise RuntimeError("simulated webhook 429")

    async def send_digest(self, *a):
        raise RuntimeError("simulated webhook 5xx")


async def test_send_failure_does_not_abort_run_and_state_is_persisted(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    config = _config(tmp_path)
    _prime_seen(config)

    # A raising notifier must not abort the run; the error is recorded and state saved.
    stats = await pipeline.run(config, provider=FakeProvider(value=90), notifier=RaisingNotifier(), now=NOW)
    assert stats["errors"] >= 1
    import os
    assert os.path.exists(config.settings.seen_path)  # saved -> delivered items won't re-fire

    # Next run: the posting is already seen, so it is not retried/re-sent.
    stats2 = await pipeline.run(config, provider=FakeProvider(value=90), notifier=RaisingNotifier(), now=NOW)
    assert stats2["new"] == 0


async def test_intra_run_uid_dedup_pings_once(tmp_path, monkeypatch):
    intern = _postings()[0]

    async def fake_fetch_all(companies, **kw):
        return [intern, intern], []  # same uid twice in one poll (e.g. pagination overlap)

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    config = _config(tmp_path)
    _prime_seen(config)

    notifier = FakeNotifier()
    await pipeline.run(config, provider=FakeProvider(value=90), notifier=notifier, now=NOW)
    assert len(notifier.ones) == 1  # deduped -> pinged once, not twice


class FakeSink:
    def __init__(self):
        self.added = []

    def add(self, posting, score):
        self.added.append(posting.uid)
        return True

    def flush(self):  # batched write happens here; returns rows written
        return len(self.added)


async def test_pipeline_mirrors_matches_to_sheet(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    config = _config(tmp_path)
    _prime_seen(config)

    sink = FakeSink()
    stats = await pipeline.run(config, provider=FakeProvider(value=90, reason="r"),
                               notifier=FakeNotifier(), sheet_sink=sink, now=NOW)

    assert sink.added == ["greenhouse:c:1"]  # the intern is mirrored; the senior was rules-filtered
    assert stats["tracked"] == 1


async def test_sheet_failure_does_not_abort_run(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    config = _config(tmp_path)
    _prime_seen(config)

    class RaisingSink:
        def add(self, posting, score):
            return True

        def flush(self):
            raise RuntimeError("simulated Sheets 503")

    notifier = FakeNotifier()
    stats = await pipeline.run(config, provider=FakeProvider(value=90), notifier=notifier,
                               sheet_sink=RaisingSink(), now=NOW)
    assert stats["errors"] >= 1
    assert len(notifier.ones) == 1   # the Discord ping still fired despite the Sheet error
    assert stats["tracked"] == 0


async def test_backfill_writes_to_sheet_without_touching_state(tmp_path, monkeypatch):
    import os

    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    config = _config(tmp_path)  # no _prime_seen: backfill ignores the seen-set entirely

    sink = FakeSink()
    stats = await pipeline.backfill(config, provider=FakeProvider(value=90, reason="r"),
                                    sheet_sink=sink, now=NOW)
    assert sink.added == ["greenhouse:c:1"]   # intern only; the senior was rules-filtered
    assert stats["tracked"] == 1
    assert not os.path.exists(config.settings.seen_path)  # seen-set never written


async def test_backfill_requires_a_sheet(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    config = _config(tmp_path)  # settings.sheet_id is None -> no sink can be built
    with pytest.raises(SystemExit):
        await pipeline.backfill(config, provider=FakeProvider(value=90))


class ErrorProvider:
    """Mimics an LLM outage / 429: returns a zero score flagged not-ok."""
    async def score(self, posting, profile):
        return Score(0, "LLM error: 429", ok=False)


async def test_error_score_pings_dream_but_is_not_written_to_sheet(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return _postings(), []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    # Dream tier: classify() routes to HIGH regardless of score, so without the ok-gate
    # a failed score would land in the Sheet as a bogus Fit 0 row.
    profile = Profile(summary="s", title_include=["intern"], title_exclude=["senior"],
                      locations_allow=["toronto"], locations_block=[], freshness_days=21)
    companies = [Company(slug="c", ats="greenhouse", tier="dream")]
    settings = Settings(webhook_url=None, llm_api_key=None, llm_model="m", llm_provider="gemini",
                        role_id=None, seen_path=str(tmp_path / "seen.json"), dry_run=True)
    config = Config(profile, companies, settings)
    _prime_seen(config)

    sink = FakeSink()
    notifier = FakeNotifier()
    stats = await pipeline.run(config, provider=ErrorProvider(), notifier=notifier,
                               sheet_sink=sink, now=NOW)
    assert sink.added == []             # error score never written, even for a dream company
    assert stats["score_errors"] == 1   # the failure is surfaced in stats, not silent
    assert len(notifier.ones) == 1      # dream-tier still pings Discord as a heads-up
