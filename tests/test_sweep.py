from datetime import datetime, timedelta, timezone

from job_radar import coverage, pipeline, rolekey, sheet
from job_radar.config import Config, Settings
from job_radar.models import Company, Posting, Profile, Score
from job_radar.scorecache import ScoreCache
from job_radar.scorer import FakeProvider

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def P(uid, title, ats="greenhouse", company="c", loc="Toronto", age=40):
    return Posting(uid=uid, ats=ats, company=company, title=title, location=loc,
                   url="https://x/" + uid, posted_at=NOW - timedelta(days=age), description="d")


def _config(tmp_path):
    profile = Profile(summary="s", title_include=["intern"], title_exclude=["senior"],
                      locations_allow=["toronto"], locations_block=[], freshness_days=21)
    settings = Settings(webhook_url=None, llm_api_key=None, llm_model="m", llm_provider="gemini",
                        role_id=None, seen_path=str(tmp_path / "seen.json"), dry_run=True)
    return Config(profile, [Company(slug="c", ats="greenhouse")], settings)


class Sink:
    def __init__(self, tracked=(), roles=()):
        self.added, self._tracked, self._roles = [], set(tracked), set(roles)
        self.ws = None

    def is_tracked(self, uid):
        return uid in self._tracked

    def is_duplicate_role(self, p):
        return rolekey.key(p.company, p.title) in self._roles

    def add(self, p, score):
        self.added.append(p.uid)
        return True

    def flush(self):
        return len(self.added)


class CountingProvider(FakeProvider):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.calls = 0

    async def score(self, posting, profile):
        self.calls += 1
        return await super().score(posting, profile)


def test_rolekey_unpacks_feeds_and_drops_season():
    assert rolekey.key("simplify", "Pebl: SWE Intern (Summer 2027)") == rolekey.key("pebl", "SWE Intern")
    assert rolekey.key("Royal Bank of Canada", "x") == rolekey.key("royalbankof", "x")


def test_scorecache_roundtrip_and_prune(tmp_path):
    c = ScoreCache(str(tmp_path / "s.json"))
    c.put("a", 40, NOW - timedelta(days=200))
    c.put("b", 70, NOW)
    c.save(now=NOW)
    d = ScoreCache(str(tmp_path / "s.json")).load()
    assert "a" not in d and d.fit("b") == 70


async def test_sweep_scores_old_unseen_roles_once(tmp_path, monkeypatch):
    posts = [P("greenhouse:c:1", "Software Intern", age=40),        # too old for the radar
             P("greenhouse:c:2", "Senior Engineer"),                # rules-filtered
             P("greenhouse:c:3", "ML Intern"),                      # already in the Sheet
             P("greenhouse:c:4", "Data Intern"),                    # scored before
             P("simplify:simplify:9", "c: Software Intern", ats="simplify", company="simplify"),
             P("greenhouse:c:5", "Winter 2027 Intern")]             # term he can't take

    async def fake_fetch_all(companies, **kw):
        return posts, []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(sheet, "write_top", lambda *a, **k: 0)
    monkeypatch.setattr(sheet, "all_records", lambda ws: [])
    cache = ScoreCache(str(tmp_path / "scored.json"))
    cache.put("greenhouse:c:4", 30, NOW)
    sink = Sink(tracked={"greenhouse:c:3"})
    prov = CountingProvider(value=80, reason="r")
    stats = await pipeline.sweep(_config(tmp_path), provider=prov, sheet_sink=sink,
                                 cache=cache, now=NOW)
    # the feed copy of 'Software Intern' and the board posting are one role: one call,
    # and the company's own board wins
    assert sink.added == ["greenhouse:c:1"]
    assert prov.calls == 1
    assert stats["tracked"] == 1
    assert "greenhouse:c:1" in cache

    # a second sweep finds nothing new to score
    prov2 = CountingProvider(value=80)
    await pipeline.sweep(_config(tmp_path), provider=prov2, sheet_sink=Sink(tracked={"greenhouse:c:1", "greenhouse:c:3"}),
                         cache=ScoreCache(str(tmp_path / "scored.json")).load(), now=NOW)
    assert prov2.calls == 0


async def test_sweep_skips_low_scores_but_remembers_them(tmp_path, monkeypatch):
    async def fake_fetch_all(companies, **kw):
        return [P("greenhouse:c:1", "Software Intern")], []

    monkeypatch.setattr(pipeline, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(sheet, "write_top", lambda *a, **k: 0)
    monkeypatch.setattr(sheet, "all_records", lambda ws: [])
    cache = ScoreCache(str(tmp_path / "scored.json"))
    sink = Sink()
    await pipeline.sweep(_config(tmp_path), provider=FakeProvider(value=40), sheet_sink=sink,
                         cache=cache, now=NOW)
    assert sink.added == [] and cache.fit("greenhouse:c:1") == 40


def test_coverage_report_buckets():
    from pathlib import Path
    prof = _config(Path(".")).profile
    posts = [P("simplify:simplify:1", "a: Software Intern", ats="simplify", company="simplify", age=1),
             P("simplify:simplify:2", "b: Data Intern", ats="simplify", company="simplify", age=1),
             P("canadalist:canadalist:3", "d: ML Intern", ats="canadalist", company="canadalist", age=1),
             P("greenhouse:c:4", "Board Intern", age=1)]          # not an answer-key source
    rows = [{"Company": "a", "Role": "Software Intern"}]
    cache = ScoreCache("unused")
    cache.put("simplify:simplify:2", 20)
    r = coverage.report(posts, rows, cache, prof, NOW)
    assert (r["in_sheet"], r["scored_low"], r["never_scored"]) == (1, 1, 1)
    assert r["coverage_pct"] == 66.7
    assert r["missing_examples"] == ["d: ML Intern"]


class WS:
    def __init__(self, rows):
        self.rows = rows

    def row_values(self, n):
        return sheet.HEADERS

    def col_values(self, c):
        return ["uid"] + [r["uid"] for r in self.rows]

    def get_all_records(self):
        return self.rows


def test_sheetsink_skips_feed_copies_and_same_city_duplicates():
    ws = WS([{"uid": "greenhouse:lyft:1", "Company": "lyft", "Role": "ML Intern",
              "Location": "Toronto", "Status": "New", "Applied": "FALSE"}])
    sink = sheet.SheetSink(ws, added_on="2026-10-29")
    s = Score(value=80, reason="r")
    assert not sink.add(P("simplify:simplify:9", "Lyft: ML Intern (Summer 2027)", ats="simplify",
                          company="simplify", loc="Montreal"), s)
    assert not sink.add(P("greenhouse:lyft:2", "ML Intern", company="lyft", loc="Toronto"), s)
    assert sink.add(P("greenhouse:lyft:3", "ML Intern", company="lyft", loc="Montreal"), s)
