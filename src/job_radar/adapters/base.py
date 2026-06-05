import html
import re
from datetime import datetime, timezone

from dateutil import parser as dateparser

USER_AGENT = "job-radar/0.1 (+https://github.com/job-radar)"
TIMEOUT = 20.0


async def get_json(client, url, *, method="GET", json_body=None):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    resp = await client.request(method, url, headers=headers, json=json_body, timeout=TIMEOUT)
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
