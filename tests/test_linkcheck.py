import httpx

from job_radar import linkcheck
from job_radar.linkcheck import CLOSED, OPEN, UNKNOWN, Checker
from job_radar.pipeline import check_links
from job_radar.sheet import TOP_TAB, all_records, write_top


def test_workday_api_from_job_page():
    url = ("https://soti.wd3.myworkdayjobs.com/SOTI-Next-Gen/job/Mississauga-Canada/"
           "Data-Scientist--Intern_R10571")
    assert linkcheck.workday_api(url) == (
        "https://soti.wd3.myworkdayjobs.com/wday/cxs/soti/SOTI-Next-Gen/job/Mississauga-Canada/"
        "Data-Scientist--Intern_R10571")


def test_workday_api_drops_locale_and_handles_myworkdaysite():
    assert linkcheck.workday_api(
        "https://manulife.wd3.myworkdayjobs.com/en-US/MFCJH_adminJobs/job/Toronto/AI_JR1-1"
    ) == "https://manulife.wd3.myworkdayjobs.com/wday/cxs/manulife/MFCJH_adminJobs/job/Toronto/AI_JR1-1"
    assert linkcheck.workday_api(
        "https://wd5.myworkdaysite.com/recruiting/vhr_fhlbdm/careers/job/Des-Moines/Co-Op_REQ1"
    ) == "https://wd5.myworkdaysite.com/wday/cxs/vhr_fhlbdm/careers/job/Des-Moines/Co-Op_REQ1"
    assert linkcheck.workday_api("https://example.com/careers/job/1") is None


def test_refs_from_links_and_uids():
    assert linkcheck.greenhouse_ref("https://job-boards.greenhouse.io/drweng/jobs/7991196", "") \
        == ("drweng", "7991196")
    # custom domain (CareerPuck) still checks through the uid
    assert linkcheck.greenhouse_ref("https://app.careerpuck.com/job-board/lyft/job/87?gh_jid=87",
                                    "greenhouse:lyft:87") == ("lyft", "87")
    assert linkcheck.lever_ref("https://jobs.lever.co/eqbank/1c46-aa/", "") == ("eqbank", "1c46-aa")
    assert linkcheck.ashby_ref("https://jobs.ashbyhq.com/bree/42FE/application?embed=true") \
        == ("bree", "42fe")
    assert linkcheck.smartrecruiters_ref("https://jobs.smartrecruiters.com/ServiceNow/744000161936128") \
        == ("ServiceNow", "744000161936128")
    assert "Id=%22304178%22,siteNumber=CX_1" in linkcheck.oracle_api(
        "https://iaziqy.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/304178")


def test_dead_text_matches_closed_pages_but_not_footers():
    assert linkcheck.dead_text("<h1>Sorry, this job is no longer available.</h1>")
    assert linkcheck.dead_text("<p>This position has been filled</p>")
    assert linkcheck.dead_text("The page you are looking for doesn’t exist.")
    assert linkcheck.dead_text("<p>Can't find the job you are looking for? Join our talent "
                               "community.</p><h1>ML Intern</h1>") is None


def _client(routes):
    def handler(request):
        url = str(request.url)
        for prefix, resp in routes.items():
            if url.startswith(prefix):
                return resp(request) if callable(resp) else resp
        return httpx.Response(599)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_checker_verdicts():
    wd = "https://t.wd3.myworkdayjobs.com"
    routes = {
        f"{wd}/wday/cxs/t/Site/job/A/Open_R1": httpx.Response(
            200, json={"jobPostingInfo": {"title": "x", "canApply": True}}),
        f"{wd}/wday/cxs/t/Site/job/A/Gone_R2": httpx.Response(404),
        "https://boards-api.greenhouse.io/v1/boards/lyft/jobs/1": httpx.Response(404),
        "https://boards-api.greenhouse.io/v1/boards/lyft/jobs/2": httpx.Response(200, json={}),
        "https://api.ashbyhq.com/posting-api/job-board/bree": httpx.Response(
            200, json={"jobs": [{"id": "AAA"}]}),
        "https://dead.example.com/": httpx.Response(200, text="<h1>This job has expired</h1>"),
        "https://live.example.com/": httpx.Response(200, text="<h1>ML Intern</h1> Apply now"),
        "https://gone.example.com/": httpx.Response(404),
        "https://blocked.example.com/": httpx.Response(403),
        "https://go.short/1": lambda req: httpx.Response(
            302, headers={"Location": f"{wd}/Site/job/A/Gone_R2"}),
        f"{wd}/Site/job/A/Gone_R2": httpx.Response(200, text="<div id=root></div>"),
    }
    async with _client(routes) as client:
        c = Checker(client)
        assert (await c.check(f"{wd}/en-US/Site/job/A/Open_R1")).verdict == OPEN
        assert (await c.check(f"{wd}/Site/job/A/Gone_R2")).verdict == CLOSED
        assert (await c.check("https://app.careerpuck.com/x?gh_jid=1", "greenhouse:lyft:1")).verdict == CLOSED
        assert (await c.check("https://app.careerpuck.com/x?gh_jid=2", "greenhouse:lyft:2")).verdict == OPEN
        assert (await c.check("https://jobs.ashbyhq.com/bree/aaa")).verdict == OPEN
        assert (await c.check("https://jobs.ashbyhq.com/bree/bbb/application")).verdict == CLOSED
        assert (await c.check("https://dead.example.com/")).verdict == CLOSED
        assert (await c.check("https://live.example.com/")).verdict == OPEN
        assert (await c.check("https://gone.example.com/")).verdict == CLOSED
        assert (await c.check("https://blocked.example.com/")).verdict == UNKNOWN
        assert (await c.check("")).verdict == UNKNOWN
        via = await c.check("https://go.short/1")
        assert via.verdict == CLOSED and "redirect" in via.reason


async def test_checker_backs_off_on_429():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429)
        return httpx.Response(404)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        res = await Checker(client, backoff=0).check("https://t.wd1.myworkdayjobs.com/S/job/A/B_R1")
    assert res.verdict == CLOSED and len(calls) == 3


def test_careerpuck_maps_to_greenhouse():
    assert linkcheck.greenhouse_ref(
        "https://app.careerpuck.com/job-board/lyft/job/8767710002?gh_jid=8767710002", "") \
        == ("lyft", "8767710002")


async def test_checker_network_error_is_unknown():
    def boom(request):
        raise httpx.ConnectTimeout("slow")
    async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
        assert (await Checker(client).check("https://x.example.com/job/1")).verdict == UNKNOWN


def test_open_rows_skips_acted_on_rows():
    recs = [
        {"Link": "https://a", "Status": "New", "Applied": "FALSE"},
        {"Link": "https://b", "Status": "Closed"},
        {"Link": "https://c", "Status": "New", "Applied": "TRUE"},
        {"Link": "https://d", "Status": "Skip"},
        {"Link": "", "Status": "New"},
        {"Link": "https://f", "Status": ""},
    ]
    assert [i for i, _ in linkcheck.open_rows(recs)] == [2, 7]


class FakeWS:
    title = "Sheet1"

    def __init__(self, header, records):
        self.header, self.records, self.updates = header, records, None
        self.spreadsheet = None

    def row_values(self, n):
        return self.header

    def get_all_records(self):
        return self.records

    def batch_update(self, updates, value_input_option=None):
        self.updates = updates


class _Cfg:
    def __init__(self, dry):
        # settings.dry_run is true in CI (no webhook); check_links must ignore it
        self.settings = type("S", (), {"dry_run": True})()


async def test_check_links_writes_status_column_and_respects_dry_run(monkeypatch):
    import job_radar.sheet as sheet_mod
    monkeypatch.setattr(sheet_mod, "write_top", lambda *a, **k: 0)
    header = ["uid", "Company", "Role", "Applied", "Link", "Status"]
    recs = [{"uid": "", "Company": "a", "Role": "r", "Applied": "FALSE",
             "Link": "https://gone.example.com/", "Status": "New"},
            {"uid": "", "Company": "b", "Role": "r", "Applied": "FALSE",
             "Link": "https://live.example.com/", "Status": "New"}]
    routes = {"https://gone.example.com/": httpx.Response(404),
              "https://live.example.com/": httpx.Response(200, text="Apply")}
    async with _client(routes) as client:
        ws = FakeWS(header, recs)
        out = await check_links(_Cfg(False), dry_run=True, ws=ws, client=client)
        assert out["closed"] == 1 and out["written"] == 0 and ws.updates is None
        out = await check_links(_Cfg(False), ws=ws, client=client)
        assert out["written"] == 1 and ws.updates == [{"range": "F2", "values": [["Closed"]]}]


async def test_check_links_refuses_mass_close(monkeypatch):
    header = ["Link", "Status"]
    recs = [{"Link": f"https://gone.example.com/{i}", "Status": "New"} for i in range(5)]
    async with _client({"https://gone.example.com/": httpx.Response(404)}) as client:
        ws = FakeWS(header, recs)
        out = await check_links(_Cfg(False), ws=ws, client=client)
        assert out["closed"] == 5 and out["written"] == 0 and ws.updates is None


def test_all_records_returns_strings_so_top30_cannot_crash():
    class WS:
        title = "Sheet1"
        def get_all_records(self):
            return [{"Company": 2027, "Role": 2027, "Lane": 0, "Fit": 80, "Status": "New",
                     "Link": "https://x"}]
    recs = all_records(WS())
    assert recs[0]["Company"] == "2027" and recs[0]["Lane"] == "0"

    class Tab:
        def clear(self):
            pass
        def update(self, range_name, values, value_input_option):
            self.values = values

    class Book:
        tab = Tab()
        def worksheet(self, title):
            assert title == TOP_TAB
            return self.tab

    ws = WS()
    ws.spreadsheet = Book()
    assert write_top(ws) == 1


def test_closed_updates_follow_rows_that_moved():
    header = ["uid", "Link", "Status"]
    a = {"uid": "u1", "Link": "https://a", "Status": "New"}
    b = {"uid": "u2", "Link": "https://b", "Status": "New"}
    results = [(2, a, linkcheck.Result(CLOSED, "x")), (3, b, linkcheck.Result(OPEN, "y"))]
    # a sort moved a down to row 3; a row Taka just marked Applied is left alone
    assert linkcheck.closed_updates(results, header, [b, a]) == [{"range": "C3", "values": [["Closed"]]}]
    applied = dict(a, Status="Applied")
    assert linkcheck.closed_updates(results, header, [b, applied]) == []


async def test_workday_checks_the_board_search():
    wd = "https://t.wd3.myworkdayjobs.com"
    seen = []

    def handler(request):
        if request.method == "POST":
            import json
            q = json.loads(request.content)["searchText"]
            seen.append(q)
            hits = [{"externalPath": "/job/Toronto/AI-Co-op_R-0000187401-2"}] if q == "R-0000187401" else []
            return httpx.Response(200, json={"jobPostings": hits})
        return httpx.Response(429)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        c = Checker(client, backoff=0)
        live = await c.check(f"{wd}/en-US/Site/job/Toronto/AI-Co-op_R-0000187401-1")
        gone = await c.check(f"{wd}/Site/job/Toronto/Old-Co-op_R999")
    assert live.verdict == OPEN and gone.verdict == CLOSED
    assert seen == ["R-0000187401", "R999", "Old Co op"]
