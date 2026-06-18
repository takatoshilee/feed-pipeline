from job_radar import discover as disc
from job_radar.models import Company, Posting, Profile


def _profile():
    return Profile(summary="python react full-stack", title_include=["intern", "software"],
                   title_exclude=["senior"], locations_allow=["toronto", "remote"],
                   locations_block=[], freshness_days=21)


def _post(title, desc=""):
    return Posting(uid="x:1", ats="greenhouse", company="x", title=title, location="Toronto",
                   url="u", posted_at=None, description=desc)


class _FakeAdapter:
    def __init__(self, posts):
        self.posts = posts

    async def fetch(self, client, company):
        return self.posts


async def test_is_relevant_requires_a_promising_role(monkeypatch):
    prof = _profile()
    # early-career SWE role -> heuristic well above the gate
    monkeypatch.setitem(disc.ADAPTERS, "greenhouse", _FakeAdapter([_post("Software Engineer Intern", "python react")]))
    assert await disc.is_relevant(None, Company(slug="g", ats="greenhouse"), prof) is True
    # full-time 'Software Developer' passes rules (has 'software') but scores below the gate
    monkeypatch.setitem(disc.ADAPTERS, "greenhouse", _FakeAdapter([_post("Software Developer")]))
    assert await disc.is_relevant(None, Company(slug="g", ats="greenhouse"), prof) is False
    # no matching role at all
    monkeypatch.setitem(disc.ADAPTERS, "greenhouse", _FakeAdapter([_post("Senior Marketing Manager")]))
    assert await disc.is_relevant(None, Company(slug="g", ats="greenhouse"), prof) is False


async def test_board_qualifies_is_wider_than_is_relevant(monkeypatch):
    prof = _profile()   # include=[intern, software], exclude=[senior]
    # A full-time 'Software Developer' is below the strict heuristic gate, but it IS a tech
    # employer, so the WIDE gate keeps the board (it may post a co-op later).
    monkeypatch.setitem(disc.ADAPTERS, "greenhouse", _FakeAdapter([_post("Software Developer")]))
    g = Company(slug="g", ats="greenhouse")
    assert await disc.board_qualifies(None, g, prof) is True
    assert await disc.is_relevant(None, g, prof) is False
    # Only a senior (excluded) role -> not kept by either gate.
    monkeypatch.setitem(disc.ADAPTERS, "greenhouse", _FakeAdapter([_post("Senior Software Engineer")]))
    assert await disc.board_qualifies(None, g, prof) is False
    # Pure non-tech board -> skipped by the wide gate too.
    monkeypatch.setitem(disc.ADAPTERS, "greenhouse", _FakeAdapter([_post("Marketing Coordinator")]))
    assert await disc.board_qualifies(None, g, prof) is False


def _cfg(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text("companies:\n  - {slug: existing, ats: greenhouse, tier: target}\n")
    prof = tmp_path / "p.yaml"
    prof.write_text("summary: s\ntitle_include: [intern]\ntitle_exclude: []\n"
                    "locations_allow: []\nlocations_block: []\nfreshness_days: 21\n")
    return str(cfg), str(prof)


def test_extract_pulls_ats_slug_from_urls():
    assert disc._extract("https://boards.greenhouse.io/airbnb/jobs/123") == ("greenhouse", "airbnb")
    assert disc._extract("https://jobs.lever.co/figma/abc") == ("lever", "figma")
    assert disc._extract("https://jobs.ashbyhq.com/cohere") == ("ashby", "cohere")
    assert disc._extract("https://example.com/careers") is None


async def test_discover_adds_only_relevant_new_companies(tmp_path, monkeypatch):
    cfg, prof = _cfg(tmp_path)

    async def fake_mine(client):
        return {"existing": "greenhouse", "good": "ashby", "bad": "lever"}

    async def fake_relevant(client, company, profile):
        return company.slug == "good"   # only 'good' currently has a matching role

    monkeypatch.setattr(disc, "mine_candidates", fake_mine)
    monkeypatch.setattr(disc, "is_relevant", fake_relevant)

    merged, added, _ = await disc.discover(cfg, prof, max_add=40, max_probe=100, client=object())
    slugs = {c["slug"] for c in merged}
    assert added == 1
    assert "good" in slugs            # relevant + new -> added
    assert "bad" not in slugs         # new but not relevant -> skipped
    assert "existing" in slugs        # already tracked -> untouched, not duplicated
    assert sum(c["slug"] == "existing" for c in merged) == 1


async def test_discover_respects_max_add(tmp_path, monkeypatch):
    cfg, prof = _cfg(tmp_path)

    async def fake_mine(client):
        return {f"co{i}": "ashby" for i in range(10)}

    async def fake_relevant(client, company, profile):
        return True   # all relevant

    monkeypatch.setattr(disc, "mine_candidates", fake_mine)
    monkeypatch.setattr(disc, "is_relevant", fake_relevant)

    merged, added, _ = await disc.discover(cfg, prof, max_add=3, max_probe=100, client=object())
    assert added == 3   # capped, even though 10 were relevant
