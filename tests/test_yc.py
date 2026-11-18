import httpx
import yaml

from job_radar import yc

PEBL = {"name": "Pebl", "slug": "pebl", "website": "https://www.pebl.com", "status": "Active"}
LOOP = {"name": "Loop", "slug": "loop-2", "website": "https://loop.inc", "status": "Active"}


def test_candidates_cover_slug_name_and_domain():
    c = yc.candidates({"name": "Hex Technologies", "slug": "hex", "website": "https://hex.tech/x"})
    assert c[0] == "hex"
    assert "hextechnologies" in c and "hex-technologies" in c
    assert len(c) == len(set(c))


def test_mentions_needs_whole_name_or_domain():
    assert yc.mentions(PEBL, '{"content": "Join Pebl in Toronto"}')
    assert yc.mentions(PEBL, '{"url": "https://pebl.com/jobs/1"}')
    assert not yc.mentions(PEBL, '{"content": "pebbles and peblet"}')


def test_tag_existing_marks_watched_yc_boards():
    existing = [{"slug": "pebl", "ats": "ashby", "tier": "target"},
                {"slug": "stripe", "ats": "greenhouse", "tier": "target"},
                {"slug": "simplify", "ats": "simplify", "tier": "target"}]
    missing, tagged = yc.tag_existing(existing, [PEBL, LOOP])
    assert [c["slug"] for c in tagged] == ["pebl"]
    assert existing[0]["yc"] is True and "yc" not in existing[1]
    assert missing == [LOOP]


def _transport(boards):
    """boards: {(host_fragment, slug): json}. Everything else 404s."""
    def handler(request):
        url = str(request.url)
        if "yc-oss" in url:
            return httpx.Response(200, json=[PEBL, LOOP])
        for (host, slug), body in boards.items():
            if host in url and f"/{slug}" in url:
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


async def test_run_adds_verified_boards_and_skips_foreign_ones(tmp_path):
    cfg = tmp_path / "companies.yaml"
    cfg.write_text(yaml.safe_dump({"companies": [{"slug": "stripe", "ats": "greenhouse"}]}))
    boards = {
        ("ashbyhq", "pebl"): {"jobs": [{"id": "1", "title": "SWE Intern",
                                        "descriptionHtml": "At Pebl we build..."}]},
        # a 'loop' greenhouse board that belongs to some other company
        ("greenhouse", "loop"): {"jobs": [{"id": 9, "title": "Driver", "content": "Acme Corp"}]},
    }
    async with httpx.AsyncClient(transport=_transport(boards)) as client:
        stats = await yc.run(str(cfg), client=client)
    assert stats["added"] == 1 and stats["no_board_found"] == 1
    data = yaml.safe_load(cfg.read_text())["companies"]
    assert {"slug": "pebl", "ats": "ashby", "tier": "target", "yc": True} in data
    assert all(c["slug"] != "loop" for c in data)


async def test_dry_run_writes_nothing(tmp_path):
    cfg = tmp_path / "companies.yaml"
    cfg.write_text(yaml.safe_dump({"companies": []}))
    before = cfg.read_text()
    boards = {("ashbyhq", "pebl"): {"jobs": [{"id": "1", "title": "x", "descriptionHtml": "Pebl"}]}}
    async with httpx.AsyncClient(transport=_transport(boards)) as client:
        stats = await yc.run(str(cfg), dry_run=True, client=client)
    assert stats["added"] == 1
    assert cfg.read_text() == before
