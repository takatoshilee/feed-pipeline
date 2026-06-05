from datetime import datetime, timezone, timedelta

import httpx

from job_radar.models import Posting, Score, Company, Urgency
from job_radar.notify import build_embed, DiscordNotifier, ConsoleNotifier

NOW = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
COMPANY = Company(slug="cohere", ats="ashby", tier="dream")


def _p():
    return Posting(uid="x:1", ats="ashby", company="cohere", title="ML Intern",
                   location="Toronto", url="https://job", posted_at=NOW - timedelta(hours=1),
                   description="d")


def test_build_embed_shape_and_color():
    e = build_embed(_p(), Score(90, "great fit", ["ai"]), Urgency.HIGH, COMPANY, NOW)
    assert e["title"] == "ML Intern"
    assert e["url"] == "https://job"
    assert e["color"] == 0xE74C3C
    names = [f["name"] for f in e["fields"]]
    assert any("Fit 90" in n for n in names)


async def test_discord_notifier_pings_role_on_high():
    sent = {}

    def handler(request):
        sent["body"] = request.read().decode()
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        n = DiscordNotifier("https://hook", role_id="999", client=client)
        await n.send_one(_p(), Score(90, "r"), Urgency.HIGH, COMPANY, NOW)

    assert "<@&999>" in sent["body"]


async def test_console_notifier_runs(capsys):
    n = ConsoleNotifier()
    await n.send_one(_p(), Score(90, "r"), Urgency.HIGH, COMPANY, NOW)
    await n.send_digest([(_p(), Score(55, "r"), COMPANY)], NOW)
    out = capsys.readouterr().out
    assert "ML Intern" in out
