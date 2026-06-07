from datetime import datetime, timezone

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
    settings = Settings(webhook_url=None, llm_api_key=None, llm_model="m",
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
