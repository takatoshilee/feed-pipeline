import httpx
import pytest

from job_radar.adapters import discord
from job_radar.models import Company
from job_radar import rolekey

# Shape of a Nordique post: header embed (company), role embed with fields, footer embed,
# and an Apply link button in an action row.
NORDIQUE = {
    "id": "1", "channel_id": "123", "timestamp": "2026-11-26T21:34:00+00:00",
    "author": {"username": "Nordique", "bot": True},
    "content": "",
    "embeds": [
        {"title": "ABB"},
        {"title": "Software Intern - Winter 2027", "url": "https://abb.example/role",
         "fields": [{"name": "Level", "value": "🚀 Internship"},
                    {"name": "Sponsorship", "value": "🤔 Unknown"},
                    {"name": "Location", "value": "Montreal, QC"}]},
        {"description": "made w/ love in/eysk-io"},
    ],
    "components": [{"type": 1, "components": [
        {"type": 2, "style": 5, "label": "Apply", "url": "https://abb.example/apply"}]}],
}

HUMAN = {"id": "2", "author": {"username": "someone"},
         "embeds": [{"title": "Not a job", "url": "https://x.example"}]}


def test_parse_nordique_post():
    ps = discord.parse([NORDIQUE, HUMAN])
    assert len(ps) == 1
    p = ps[0]
    assert p.title == "ABB: Software Intern - Winter 2027"
    assert p.ats == "discord" and p.company == "discord"
    assert p.uid.startswith("discord:discord:")
    assert p.url == "https://abb.example/apply"          # the Apply button wins
    assert p.location == "Montreal, QC"
    assert "Level: 🚀 Internship" in p.description and "Sponsorship" in p.description
    assert p.posted_at is not None and p.posted_at.year == 2026


def test_falls_back_to_embed_url_and_skips_linkless():
    msg = dict(NORDIQUE, components=[])
    assert discord.parse([msg])[0].url == "https://abb.example/role"
    bare = dict(NORDIQUE, components=[], embeds=[{"title": "ABB"}, {"title": "Role", "fields": [{"name": "Level", "value": "x"}]}])
    assert discord.parse([bare]) == []


def test_rolekey_unpacks_real_company():
    p = discord.parse([NORDIQUE])[0]
    assert rolekey.split(p.company, p.title) == ("ABB", "Software Intern - Winter 2027")


@pytest.mark.asyncio
async def test_fetch_reads_each_channel(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t")
    monkeypatch.setenv("DISCORD_JOB_CHANNELS", "123, 456,bad")
    seen = []

    def handler(req):
        seen.append(str(req.url))
        assert req.headers["Authorization"] == "Bot t"
        return httpx.Response(200, json=[NORDIQUE])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ps = await discord.fetch(client, Company(slug="discord", ats="discord"))
    assert len(seen) == 2 and "/channels/123/" in seen[0] and "/channels/456/" in seen[1]
    assert len(ps) == 2


@pytest.mark.asyncio
async def test_fetch_without_setup_is_empty(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.setenv("DISCORD_JOB_CHANNELS", "123")

    def handler(req):
        raise AssertionError("should not call Discord")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await discord.fetch(client, Company(slug="discord", ats="discord")) == []
