from job_radar.seed import parse_line, merge, main


def test_parse_line_valid_and_invalid():
    assert parse_line("stripe, greenhouse, dream") == {"slug": "stripe", "ats": "greenhouse", "tier": "dream"}
    assert parse_line("ramp,ashby") == {"slug": "ramp", "ats": "ashby", "tier": "target"}
    assert parse_line("acme\tlever") == {"slug": "acme", "ats": "lever", "tier": "target"}
    assert parse_line("# a comment") is None
    assert parse_line("") is None
    assert parse_line("onlyslug") is None
    assert parse_line("acme, notarealats") is None  # unknown ats rejected


def test_parse_line_workday_needs_host_and_site():
    assert parse_line("nvidia,workday,dream,wd5,NVIDIASite") == {
        "slug": "nvidia", "ats": "workday", "tier": "dream",
        "wd_host": "wd5", "wd_site": "NVIDIASite"}
    assert parse_line("nvidia,workday,dream") is None  # missing host/site -> rejected


def test_merge_dedups_and_adds():
    existing = [{"slug": "stripe", "ats": "greenhouse", "tier": "target"}]
    lines = [
        "stripe, greenhouse",          # dup -> skipped
        "ramp, ashby, target",         # new
        "stripe, lever",               # same slug, different ats -> new
        "# comment",                   # ignored
    ]
    out, added = merge(existing, lines)
    assert added == 2
    keys = {(c["slug"], c["ats"]) for c in out}
    assert ("ramp", "ashby") in keys and ("stripe", "lever") in keys


def test_main_writes_yaml(tmp_path):
    src = tmp_path / "list.csv"
    src.write_text("ramp, ashby, dream\ncohere, ashby\n# skip me\n")
    cfg = tmp_path / "companies.yaml"
    cfg.write_text("companies:\n  - {slug: stripe, ats: greenhouse, tier: target}\n")

    rc = main([str(src), str(cfg)])
    assert rc == 0

    import yaml
    data = yaml.safe_load(cfg.read_text())
    slugs = {(c["slug"], c["ats"]) for c in data["companies"]}
    assert ("stripe", "greenhouse") in slugs
    assert ("ramp", "ashby") in slugs
    assert ("cohere", "ashby") in slugs
