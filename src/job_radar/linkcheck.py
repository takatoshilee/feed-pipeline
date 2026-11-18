"""Daily dead-link check for the open rows in the tracker Sheet.

The 15-minute radar already closes a row when its posting vanishes from a board it
polled cleanly. That misses a lot: roles that came from the curated lists (their link
points at some other company's career site), boards that error, and boards where the
listing stays up but the job page says it is gone. This module opens each open row's
own link and decides whether the job still exists.

Verdicts are "open", "closed" or "unknown". Only "closed" changes the Sheet, and only
when the evidence is specific: a job API that answers 404 for that exact job, a job
page that answers 404 or 410, or a page whose text says the job is no longer available.
Timeouts, 403s, 5xx and pages that render with JavaScript are "unknown" and left alone,
because a wrong "Closed" hides a live job and that is worse than one extra dead link.
"""
import asyncio
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .adapters.base import strip_html

OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TIMEOUT = 20.0
MAX_BODY = 600_000          # characters of a page we look at
CONCURRENCY = 12
PER_SITE = 3                # at most this many requests in flight to one site (Workday 429s)
RETRIES = 3                 # extra tries after a 429 or 503, with backoff
BACKOFF = 2.0               # seconds, doubled each retry

# Text that job sites show on a closed posting. Lower-case. Kept specific on purpose:
# a footer like "can't find the job you are looking for?" must not match.
DEAD_PHRASES = (
    "no longer available",
    "no longer accepting applications",
    "is no longer accepting",
    "no longer open",
    "position has been filled",
    "role has been filled",
    "job has been filled",
    "this job has expired",
    "job posting has expired",
    "this posting has expired",
    "posting has closed",
    "posting is closed",
    "this job is closed",
    "this position is closed",
    "this position has closed",
    "job is no longer",
    "position is no longer",
    "requisition is no longer",
    "job you are trying to apply for has been filled",
    "job not found",
    "job posting not found",
    "page you are looking for doesn't exist",
    "page you are looking for does not exist",
    "page you requested could not be found",
    "applications are closed",
    "applications for this position are closed",
    "sorry, this job",
    "the job you are looking for is no longer",
    "this job is no longer",
)
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Result:
    verdict: str
    reason: str


def dead_text(text: str) -> str | None:
    """The dead-job phrase found in a page's text, or None."""
    t = _WS.sub(" ", strip_html(text or "")).lower().replace("’", "'")
    for p in DEAD_PHRASES:
        if p in t:
            return p
    return None


# ---------------------------------------------------------------- API checks per ATS

_WD_HOST = re.compile(r"^(?P<tenant>[^.]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com$")
_WD_SITE_HOST = re.compile(r"^(?P<wd>wd\d+)\.myworkdaysite\.com$")
_LOCALE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def workday_api(url: str) -> str | None:
    """The CXS detail endpoint for a Workday job page URL, or None if it isn't one.
    https://t.wd3.myworkdayjobs.com/[en-US/]Site/job/Loc/Title_R1 ->
    https://t.wd3.myworkdayjobs.com/wday/cxs/t/Site/job/Loc/Title_R1
    https://wd5.myworkdaysite.com/recruiting/t/Site/job/... -> .../wday/cxs/t/Site/job/..."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    m = _WD_HOST.match(host)
    if m:
        tenant = m.group("tenant")
        if parts and _LOCALE.match(parts[0]):
            parts = parts[1:]
    else:
        m = _WD_SITE_HOST.match(host)
        if not m or len(parts) < 2 or parts[0] != "recruiting":
            return None
        tenant, parts = parts[1], parts[2:]
        if parts and _LOCALE.match(parts[0]):
            parts = parts[1:]
    if len(parts) < 3 or "job" not in parts[1:]:
        return None
    site, rest = parts[0], parts[1:]
    return f"https://{host}/wday/cxs/{tenant}/{site}/" + "/".join(rest)


def greenhouse_ref(url: str, uid: str) -> tuple[str, str] | None:
    """(board slug, job id) for a Greenhouse job, from the link or the row's uid."""
    u = urlparse(url)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    if host.endswith("greenhouse.io") and len(parts) >= 3 and parts[1] == "jobs":
        return parts[0], parts[2]
    if uid.startswith("greenhouse:"):
        bits = uid.split(":")
        if len(bits) >= 3:
            return bits[1], bits[2]
    # CareerPuck fronts Greenhouse boards: /job-board/<slug>/job/<id>?gh_jid=<id>
    jid = (parse_qs(u.query).get("gh_jid") or [""])[0]
    if host == "app.careerpuck.com" and len(parts) >= 2 and parts[0] == "job-board" and jid:
        return parts[1], jid
    return None


def lever_ref(url: str, uid: str) -> tuple[str, str] | None:
    u = urlparse(url)
    parts = [p for p in u.path.split("/") if p]
    if (u.hostname or "").lower() == "jobs.lever.co" and len(parts) >= 2:
        return parts[0], parts[1]
    if uid.startswith("lever:"):
        bits = uid.split(":")
        if len(bits) >= 3:
            return bits[1], bits[2]
    return None


def ashby_ref(url: str) -> tuple[str, str] | None:
    u = urlparse(url)
    parts = [p for p in u.path.split("/") if p]
    if (u.hostname or "").lower() == "jobs.ashbyhq.com" and len(parts) >= 2:
        return parts[0], parts[1].lower()
    return None


def smartrecruiters_ref(url: str) -> tuple[str, str] | None:
    u = urlparse(url)
    parts = [p for p in u.path.split("/") if p]
    if (u.hostname or "").lower() == "jobs.smartrecruiters.com" and len(parts) >= 2:
        return parts[0], parts[1].split("-")[0]
    return None


_ORACLE = re.compile(r"/hcmUI/CandidateExperience/[^/]+/sites/(?P<site>[^/]+)/job/(?P<jid>\d+)")


def oracle_api(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    m = _ORACLE.search(u.path)
    if not host.endswith("oraclecloud.com") or not m:
        return None
    return (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
            f"?onlyData=true&finder=ById;Id=%22{m.group('jid')}%22,siteNumber={m.group('site')}")


def bamboo_api(url: str) -> str | None:
    u = urlparse(url)
    host = (u.hostname or "").lower()
    parts = [p for p in u.path.split("/") if p]
    if host.endswith(".bamboohr.com") and len(parts) >= 2 and parts[0] == "careers" \
            and parts[1].isdigit():
        return f"https://{host}/careers/{parts[1]}/detail"
    return None


# ------------------------------------------------------------------------- checker

class Checker:
    """Checks links with one shared httpx client. Ashby boards are fetched once per slug."""

    def __init__(self, client, backoff=BACKOFF):
        self.client = client
        self.backoff = backoff
        self._ashby: dict = {}
        self._ashby_lock = asyncio.Lock()
        self._sites: dict = {}

    def _site_sem(self, url) -> asyncio.Semaphore:
        host = (urlparse(url).hostname or "").lower()
        site = ".".join(host.split(".")[-2:])       # every *.myworkdayjobs.com shares one
        if site not in self._sites:
            self._sites[site] = asyncio.Semaphore(PER_SITE)
        return self._sites[site]

    async def _get(self, url, *, accept="application/json"):
        """GET with a per-site cap and a backoff on 429/503, which Workday sends freely."""
        r = None
        for attempt in range(RETRIES + 1):
            async with self._site_sem(url):
                r = await self.client.get(url, headers={"User-Agent": BROWSER_UA, "Accept": accept},
                                          timeout=TIMEOUT, follow_redirects=True)
            if r.status_code not in (429, 503) or attempt == RETRIES:
                return r
            ra = r.headers.get("Retry-After", "")
            wait = float(ra) if ra.isdigit() else self.backoff * (2 ** attempt)
            await asyncio.sleep(min(wait, 30.0))
        return r

    async def _status_api(self, url, label) -> Result | None:
        """For APIs that answer 404 for a job that no longer exists."""
        r = await self._get(url)
        if r.status_code in (404, 410):
            return Result(CLOSED, f"{label} {r.status_code}")
        if r.status_code == 200:
            return Result(OPEN, f"{label} 200")
        return None

    async def _workday(self, api) -> Result | None:
        r = await self._get(api)
        if r.status_code in (404, 410):
            return Result(CLOSED, f"workday api {r.status_code}")
        if r.status_code == 200:
            try:
                info = (r.json() or {}).get("jobPostingInfo") or {}
            except ValueError:
                return None
            if not info:
                return None
            if info.get("canApply") is False:
                return Result(CLOSED, "workday canApply=false")
            return Result(OPEN, "workday api 200")
        return None

    async def _ashby_ids(self, slug) -> set | None:
        key = slug.lower()
        async with self._ashby_lock:
            if key not in self._ashby:
                try:
                    r = await self._get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
                    self._ashby[key] = ({(j.get("id") or "").lower()
                                         for j in (r.json() or {}).get("jobs") or []}
                                        if r.status_code == 200 else None)
                except Exception:
                    self._ashby[key] = None
            return self._ashby[key]

    async def _api(self, url: str, uid: str) -> Result | None:
        """A verdict from the job's own ATS API when the link (or uid) names one."""
        api = workday_api(url)
        if api:
            return await self._workday(api)
        ref = greenhouse_ref(url, uid)
        if ref:
            return await self._status_api(
                f"https://boards-api.greenhouse.io/v1/boards/{ref[0]}/jobs/{ref[1]}", "greenhouse api")
        ref = lever_ref(url, uid)
        if ref:
            return await self._status_api(
                f"https://api.lever.co/v0/postings/{ref[0]}/{ref[1]}", "lever api")
        ref = ashby_ref(url)
        if ref:
            ids = await self._ashby_ids(ref[0])
            if ids is None or not ids:
                return None          # board unreadable or empty: don't guess
            return Result(OPEN, "ashby board") if ref[1] in ids else Result(CLOSED, "not on ashby board")
        ref = smartrecruiters_ref(url)
        if ref:
            r = await self._get(f"https://api.smartrecruiters.com/v1/companies/{ref[0]}/postings/{ref[1]}")
            if r.status_code in (404, 410):
                return Result(CLOSED, f"smartrecruiters api {r.status_code}")
            if r.status_code == 200:
                try:
                    active = (r.json() or {}).get("active", True)
                except ValueError:
                    return None
                return Result(OPEN, "smartrecruiters api") if active is not False \
                    else Result(CLOSED, "smartrecruiters inactive")
            return None
        api = oracle_api(url)
        if api:
            r = await self._get(api)
            if r.status_code != 200:
                return None
            try:
                items = (r.json() or {}).get("items")
            except ValueError:
                return None
            if items is None:
                return None
            return Result(OPEN, "oracle api") if items else Result(CLOSED, "oracle api: no such job")
        api = bamboo_api(url)
        if api:
            return await self._status_api(api, "bamboohr api")
        return None

    async def check(self, url: str, uid: str = "") -> Result:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return Result(UNKNOWN, "no link")
        try:
            res = await self._api(url, uid)
            if res:
                return res
            r = await self._get(url, accept="text/html,application/xhtml+xml")
            final = str(r.url)
            # A short link (go.vansh.nyc, etc.) or a redirect that lands on a known ATS:
            # ask that ATS about the real job page.
            if final != url:
                res = await self._api(final, "")
                if res:
                    return Result(res.verdict, f"{res.reason} (via redirect)")
                if "error=true" in final:
                    return Result(CLOSED, "redirected to an error page")
            if r.status_code in (404, 410):
                return Result(CLOSED, f"page {r.status_code}")
            if r.status_code != 200:
                return Result(UNKNOWN, f"page {r.status_code}")
            phrase = dead_text(r.text[:MAX_BODY])
            if phrase:
                return Result(CLOSED, f'page says "{phrase}"')
            return Result(OPEN, "page 200")
        except Exception as e:     # timeouts, TLS, bad JSON: never a reason to close
            return Result(UNKNOWN, type(e).__name__)


def _checked(v) -> bool:
    return str(v).strip().upper() in ("TRUE", "✓", "YES", "1")


def open_rows(records) -> list[tuple[int, dict]]:
    """(sheet row number, record) for rows still worth checking: not applied, and Status
    empty or New. Row 2 is the first data row."""
    out = []
    for i, r in enumerate(records, start=2):
        status = str(r.get("Status", "") or "").strip() or "New"
        if _checked(r.get("Applied")) or status != "New":
            continue
        if str(r.get("Link", "") or "").strip():
            out.append((i, r))
    return out


async def check_rows(rows, client, concurrency=CONCURRENCY) -> list[tuple[int, dict, Result]]:
    checker = Checker(client)
    sem = asyncio.Semaphore(concurrency)

    async def one(i, r):
        async with sem:
            return i, r, await checker.check(str(r.get("Link", "")), str(r.get("uid", "")))

    return list(await asyncio.gather(*(one(i, r) for i, r in rows)))


def _key(r) -> tuple:
    return (str(r.get("uid", "") or "").strip(), str(r.get("Link", "") or "").strip())


def closed_updates(results, header, fresh_records=None) -> list:
    """Sheet cell updates that set Status=Closed for the closed results.

    Checking takes minutes and other jobs (the reminder sort, Taka's own edits) can move
    rows meanwhile, so each closed row is found again in `fresh_records` (read just before
    writing) by its uid and link, and skipped if it is gone or no longer open."""
    if "Status" not in header:
        return []
    col = header.index("Status") + 1
    letters = ""
    n = col
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    where = None
    if fresh_records is not None:
        where = {}
        for i, r in open_rows(fresh_records):
            where.setdefault(_key(r), i)
    out = []
    for i, r, res in results:
        if res.verdict != CLOSED:
            continue
        row = i if where is None else where.get(_key(r))
        if row is not None:
            out.append({"range": f"{letters}{row}", "values": [["Closed"]]})
    return out


def summary(results) -> dict:
    out = {OPEN: 0, CLOSED: 0, UNKNOWN: 0}
    for _, _, res in results:
        out[res.verdict] += 1
    return out
