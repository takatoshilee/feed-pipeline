from job_radar import discover as disc


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
