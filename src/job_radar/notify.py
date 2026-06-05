from datetime import datetime

import httpx

from .models import Company, Posting, Score, Urgency

COLORS = {Urgency.HIGH: 0xE74C3C, Urgency.MEDIUM: 0xF1C40F, Urgency.LOW: 0x2ECC71}


def _age(posting: Posting, now: datetime) -> str:
    if posting.posted_at is None:
        return "unknown"
    secs = (now - posting.posted_at).total_seconds()
    if secs < 3600:
        return f"{int(secs / 60)}m ago"
    if secs < 48 * 3600:
        return f"{int(secs / 3600)}h ago"
    return f"{int(secs / 86400)}d ago"


def build_embed(posting: Posting, score: Score, urgency: Urgency,
                company: Company | None, now: datetime) -> dict:
    tier = company.tier if company else "target"
    return {
        "title": (posting.title or "(untitled)")[:240],
        "url": posting.url,
        "color": COLORS[urgency],
        "fields": [
            {"name": "Company", "value": f"{posting.company} ({posting.ats})", "inline": True},
            {"name": "Location", "value": (posting.location or "n/a")[:200], "inline": True},
            {"name": "Posted", "value": _age(posting, now), "inline": True},
            {"name": f"Fit {score.value}/100", "value": (score.reason or "n/a")[:300], "inline": False},
        ],
        "footer": {"text": (", ".join(score.tags) or tier)[:200]},
    }


class DiscordNotifier:
    def __init__(self, webhook_url: str, role_id: str | None = None, client=None):
        self.webhook_url = webhook_url
        self.role_id = role_id
        self.client = client

    async def _post(self, payload: dict) -> None:
        owns = self.client is None
        client = self.client or httpx.AsyncClient(timeout=20.0)
        try:
            r = await client.post(self.webhook_url, json=payload)
            r.raise_for_status()
        finally:
            if owns:
                await client.aclose()

    async def send_one(self, posting, score, urgency, company, now) -> None:
        content = f"<@&{self.role_id}>" if (urgency == Urgency.HIGH and self.role_id) else None
        await self._post({"content": content,
                          "embeds": [build_embed(posting, score, urgency, company, now)]})

    async def send_digest(self, items, now) -> None:
        if not items:
            return
        lines = [f"- [{p.title}]({p.url}) — {p.company} ({s.value}/100)" for (p, s, c) in items[:25]]
        await self._post({"embeds": [{
            "title": f"Daily digest — {len(items)} lower-priority matches",
            "description": "\n".join(lines)[:4000],
            "color": COLORS[Urgency.LOW],
        }]})


class ConsoleNotifier:
    """Used in dry-run / local. Prints instead of posting."""

    async def send_one(self, posting, score, urgency, company, now) -> None:
        print(f"[{urgency.value.upper()}] {posting.title} @ {posting.company} "
              f"({score.value}/100) {posting.url} :: {score.reason}")

    async def send_digest(self, items, now) -> None:
        if not items:
            return
        print(f"[DIGEST] {len(items)} lower-priority matches")
        for (p, s, c) in items:
            print(f"   - {p.title} @ {p.company} ({s.value}/100) {p.url}")
