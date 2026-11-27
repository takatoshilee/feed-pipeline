"""Job posts that a third-party Discord bot (for example Nordique from the northern.dev
server) drops into channels of my own server.

Posts reach my server either from the bot itself or through "Follow" on an announcement
channel (those arrive as webhook messages), so nothing here reads a user account. My job-radar bot reads the channels through the REST API with its bot
token (DISCORD_BOT_TOKEN, needs the Message Content intent turned on) and the channel
ids in DISCORD_JOB_CHANNELS (comma separated).

One Nordique post looks like: a header embed whose title is the company ("ABB"), one or
more role embeds with a title ("Software Intern - Winter 2027") and fields (Level,
Sponsorship, sometimes Location), a footer embed, and a link button labelled Apply.
Parsing is defensive because the exact shape is the bot's to change: a role embed is
any embed with fields or a url, the company is the nearest earlier embed title with no
fields, and the apply link is the first link button, falling back to the role's url.

Like 'simplify', everything is one pseudo-company (slug 'discord'), so the per-company
silent prime absorbs the backlog on first add and the real company goes in the title.
"""
import hashlib
import os

from ..models import Company, Posting
from .base import TIMEOUT, USER_AGENT, to_dt

API = "https://discord.com/api/v10/channels/{cid}/messages?limit=100"
LINK_BUTTON = 5           # Discord component style for a url button
_SKIP_FIELDS = {"apply", "links"}


def _channels() -> list[str]:
    raw = os.environ.get("DISCORD_JOB_CHANNELS", "")
    return [c.strip() for c in raw.split(",") if c.strip().isdigit()]


def _buttons(components) -> list[str]:
    """Every url of a link button, in order, from a message's (nested) components."""
    out = []
    for c in components or []:
        if c.get("type") == 2 and c.get("style") == LINK_BUTTON and c.get("url"):
            out.append(c["url"])
        out.extend(_buttons(c.get("components")))
    return out


def _uid(url: str, company: str, role: str) -> str:
    basis = url or f"{company}|{role}"
    return "discord:discord:" + hashlib.sha1(basis.encode()).hexdigest()[:16]


def parse_message(msg: dict) -> list[Posting]:
    embeds = msg.get("embeds") or []
    buttons = _buttons(msg.get("components"))
    posted = to_dt(msg.get("timestamp"))
    company = ""
    roles = []
    for e in embeds:
        title = (e.get("title") or "").strip()
        fields = e.get("fields") or []
        if not fields and not e.get("url"):
            if title:
                company = title       # a header embed names the company for what follows
            continue
        if title:
            roles.append((company, title, e, fields))
    out = []
    for i, (comp, role, e, fields) in enumerate(roles):
        info = {f.get("name", "").strip(): f.get("value", "").strip() for f in fields}
        url = buttons[i] if i < len(buttons) else (e.get("url") or "")
        if not url:
            continue
        location = info.get("Location") or info.get("Locations") or ""
        desc = ". ".join(f"{k}: {v}" for k, v in info.items()
                         if k and v and k.lower() not in _SKIP_FIELDS)
        if e.get("description"):
            desc = (desc + ". " if desc else "") + e["description"].strip()
        name = comp or (e.get("author") or {}).get("name", "").strip()
        out.append(Posting(
            uid=_uid(url, name, role),
            ats="discord",
            company="discord",                       # == slug; real name in the title
            title=f"{name}: {role}" if name else role,
            location=location,
            url=url,
            posted_at=posted,
            description=desc,
            raw={"company": name, "role": role, "channel": msg.get("channel_id", "")},
        ))
    return out


def parse(messages) -> list[Posting]:
    out = []
    for m in messages or []:
        if not ((m.get("author") or {}).get("bot") or m.get("webhook_id")):
            continue                                 # bot or followed-channel posts only, never people's chat
        out.extend(parse_message(m))
    return out


async def fetch(client, company: Company) -> list[Posting]:
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    channels = _channels()
    if not token or not channels:
        return []                                    # not set up yet: quietly nothing
    headers = {"User-Agent": USER_AGENT, "Authorization": f"Bot {token}"}
    out = []
    for cid in channels:
        resp = await client.get(API.format(cid=cid), headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        out.extend(parse(resp.json()))
    return out
