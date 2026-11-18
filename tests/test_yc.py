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


def test_proof_rules():
    assert yc.mentions_domain(PEBL, '{"url": "https://pebl.com/jobs/1"}')
    assert not yc.mentions_domain(PEBL, '{"content": "Join Pebl"}')
    assert yc.distinctive("HackerRank") and yc.distinctive("Apollo.io")
    assert not yc.distinctive("Pulse") and not yc.distinctive("Atlas")
    assert yc.too_big({"team_size": 12}, 2815)
    assert not yc.too_big({"team_size": 12}, 30) and not yc.too_big({}, 900)


def test_match_existing_pairs_watched_boards():
    existing = [{"slug": "pebl", "ats": "ashby", "tier": "target"},
                {"slug": "stripe", "ats": "greenhouse", "tier": "target"},
                {"slug": "simplify", "ats": "simplify", "tier": "target"}]
    missing, pairs = yc.match_existing(existing, [PEBL, LOOP])
    assert [(y["name"], e["slug"]) for y, e in pairs] == [("Pebl", "pebl")]
    assert missing == [LOOP]


async def test_existing_plain_word_entry_needs_proof(tmp_path):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(yaml.safe_dump({"companies": [
        {"slug": "pebl", "ats": "ashby"},           # proven by domain in postings
        {"slug": "loop", "ats": "greenhouse"},      # someone else's 'loop' board
        {"slug": "loop", "ats": "workday"}]}))      # no cheap proof -> untagged
    boards = {("ashbyhq", "pebl"): {"jobs": [{"id": "1", "descriptionHtml": "pebl.com"}]},
              ("greenhouse", "loop"): {"jobs": [{"id": 2, "content": "Loop trucking"}]}}
    async with httpx.AsyncClient(transport=_transport(boards)) as client:
        stats = await yc.run(str(cfg), client=client)
    data = yaml.safe_load(cfg.read_text())["companies"]
    assert [c.get("yc", False) for c in data] == [True, False, False]
    assert stats["newly_tagged"] == 1 and stats["matched_not_proven"] == 2


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
                                        "descriptionHtml": "At Pebl (pebl.com) we build..."}]},
        # a 'loop' greenhouse board that belongs to some other company
        ("greenhouse", "loop"): {"jobs": [{"id": 9, "title": "Driver", "content": "Loop is hiring"}]},
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
    boards = {("ashbyhq", "pebl"): {"jobs": [{"id": "1", "title": "x", "descriptionHtml": "pebl.com"}]}}
    async with httpx.AsyncClient(transport=_transport(boards)) as client:
        stats = await yc.run(str(cfg), dry_run=True, client=client)
    assert stats["added"] == 1
    assert cfg.read_text() == before


async def test_website_on_hosted_page_is_proof(tmp_path):
    """A plain-word name with no domain in the postings passes if the hosted board page
    links the company's website, and fails if it links someone else's."""
    def make(page_html):
        def handler(request):
            url = str(request.url)
            if "yc-oss" in url:
                return httpx.Response(200, json=[LOOP])
            if "api.ashbyhq.com" in url and url.endswith("/loop"):
                return httpx.Response(200, json={"jobs": [{"id": "1", "title": "SWE"}]})
            if url == "https://jobs.ashbyhq.com/loop":
                return httpx.Response(200, text=page_html)
            return httpx.Response(404, json={})
        return httpx.MockTransport(handler)

    for html, want in (('{"publicWebsite":"https://loop.inc"}', 1),
                       ('{"publicWebsite":"https://loop-trucking.com"}', 0)):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(yaml.safe_dump({"companies": []}))
        async with httpx.AsyncClient(transport=make(html)) as client:
            stats = await yc.run(str(cfg), dry_run=True, client=client)
        assert stats["added"] == want
