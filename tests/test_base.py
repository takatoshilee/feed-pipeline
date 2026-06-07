from datetime import datetime, timezone

import httpx

from job_radar.adapters import base


def test_to_dt_handles_iso_and_naive():
    assert base.to_dt("2026-06-01T12:00:00Z").year == 2026
    naive = base.to_dt("2026-06-01T12:00:00")
    assert naive.tzinfo is not None  # naive input is coerced to UTC
    assert base.to_dt(None) is None
    assert base.to_dt("not a date") is None


def test_from_ms():
    dt = base.from_ms(1780000000000)
    assert dt is not None and dt.tzinfo == timezone.utc
    assert base.from_ms(None) is None
    assert base.from_ms("garbage") is None


def test_strip_html_unescapes_and_strips_tags():
    assert base.strip_html("&lt;p&gt;Hi &lt;b&gt;there&lt;/b&gt;&lt;/p&gt;") == "Hi there"
    assert base.strip_html("<p>plain   text</p>") == "plain text"
    assert base.strip_html("") == ""


async def test_get_json_retries_on_transient_then_succeeds(monkeypatch):
    async def _no_sleep(_):
        return None
    monkeypatch.setattr(base.asyncio, "sleep", _no_sleep)

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)  # transient: should be retried
        return httpx.Response(200, json={"ok": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await base.get_json(client, "https://x/y")

    assert data == {"ok": True}
    assert calls["n"] == 2  # one failure + one success
