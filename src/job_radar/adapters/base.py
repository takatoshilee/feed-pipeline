import asyncio
import html
import re
from datetime import datetime, timezone

from dateutil import parser as dateparser

USER_AGENT = "job-radar/0.1 (+https://github.com/job-radar)"
TIMEOUT = 20.0
RETRY_STATUS = {429, 500, 502, 503, 504}
BACKOFF_BASE = 0.5   # seconds; small so tests stay fast, real transient errors don't care
BACKOFF_MAX = 8.0


async def get_json(client, url, *, method="GET", json_body=None, retries=2):
    """GET/POST JSON with capped exponential backoff on transient status codes."""
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = None
    for attempt in range(retries + 1):
        resp = await client.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT)
        if resp.status_code in RETRY_STATUS and attempt < retries:
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if (retry_after and retry_after.isdigit()) else BACKOFF_BASE * (2 ** attempt)
            await asyncio.sleep(min(wait, BACKOFF_MAX))
            continue
        break
    resp.raise_for_status()
    return resp.json()


def to_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = dateparser.isoparse(str(value))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def from_ms(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(s: str) -> str:
    if not s:
        return ""
    text = html.unescape(s)
    text = _TAG.sub(" ", text)
    return _WS.sub(" ", text).strip()
