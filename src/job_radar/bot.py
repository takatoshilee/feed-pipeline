"""Interactive tracker bot (v2). One always-on process that polls the boards, writes
matches to the Google Sheet, pings Discord with Applied/Skip buttons, answers slash
commands, and sends daily reminders. Run: python -m job_radar.bot  (needs .env + [bot] deps)."""
import asyncio
import os
from datetime import date, datetime, timezone

import discord
from discord.ext import tasks

from . import sheet, tracker
from .config import load_companies, load_profile
from .dedup import SeenStore
from .filters import passes_rules
from .models import Urgency
from .pipeline import _company_map, _dedup_by_uid, _score_all, build_provider
from .sources import enrich_postings, fetch_all
from .urgency import classify

# --- module state, populated in main() ---
WS = None              # gspread worksheet (the tracker sheet)
PROFILE = None
COMPANIES = []
CMAP = {}
PROVIDER = None
CHANNEL_ID = 0
SEEN_PATH = ".state/seen.json"
PING_LEVELS = {Urgency.HIGH}   # which urgencies also post a Discord ping (Sheet gets all)
COLORS = {Urgency.HIGH: 0xE74C3C, Urgency.MEDIUM: 0xF1C40F, Urgency.LOW: 0x2ECC71}


# --- interactive buttons (DynamicItem so they survive restarts and carry the uid) ---
class TrackerButton(discord.ui.DynamicItem[discord.ui.Button],
                    template=r"jr:(?P<action>applied|skip):(?P<uid>.+)"):
    def __init__(self, action: str, uid: str):
        self.action = action
        self.uid = uid
        label, style = (("Applied", discord.ButtonStyle.success) if action == "applied"
                        else ("Not for me", discord.ButtonStyle.secondary))
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"jr:{action}:{uid}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["uid"])

    async def callback(self, interaction: discord.Interaction):
        status = "Applied" if self.action == "applied" else "Skip"
        applied_on = date.today().isoformat() if self.action == "applied" else ""
        ok = await asyncio.to_thread(sheet.set_status, WS, self.uid, status, applied_on)
        await interaction.response.send_message(
            f"Marked **{status}** in your tracker." if ok else
            "Couldn't find that one in the sheet (maybe a header was renamed?).",
            ephemeral=True,
        )


def _ping_view(uid: str) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(TrackerButton("applied", uid))
    view.add_item(TrackerButton("skip", uid))
    return view


def _match_embed(posting, score, level, company) -> discord.Embed:
    e = discord.Embed(title=posting.title[:240] or "(untitled)", url=posting.url or None,
                      color=COLORS[level])
    e.add_field(name="Company", value=f"{posting.company} ({posting.ats})", inline=True)
    e.add_field(name="Location", value=(posting.location or "n/a")[:200], inline=True)
    e.add_field(name=f"Fit {score.value}/100", value=(score.reason or "n/a")[:300], inline=False)
    return e


def _list_embed(title: str, rows) -> discord.Embed:
    if not rows:
        return discord.Embed(title=title, description="Nothing here right now.", color=0x2ECC71)
    lines = []
    for r in rows[:15]:
        link = f"[{r.get('Role', '?')}]({r.get('Link')})" if r.get("Link") else r.get("Role", "?")
        extra = f" · due {r['Deadline']}" if r.get("Deadline") else ""
        lines.append(f"- {link} — {r.get('Company', '?')} ({tracker.fit(r)}/100){extra}")
    return discord.Embed(title=title, description="\n".join(lines)[:4000], color=0xF1C40F)


bot = discord.Client(intents=discord.Intents.default())  # non-privileged only; no approval needed
tree = discord.app_commands.CommandTree(bot)


@bot.event
async def on_ready():
    print(f"job-radar bot online as {bot.user} (watching {len(COMPANIES)} boards)")


async def _post_ping(posting, score, level, company):
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    await channel.send(embed=_match_embed(posting, score, level, company), view=_ping_view(posting.uid))


@tasks.loop(minutes=15)
async def poll_loop():
    now = datetime.now(timezone.utc)
    postings, errors = await fetch_all(COMPANIES)
    postings = _dedup_by_uid(postings)
    seen = SeenStore(SEEN_PATH).load()
    new = [p for p in postings if seen.is_new(p)]

    if seen.is_empty():  # cold start: prime silently, no sheet rows / pings
        for p in postings:
            seen.mark(p, now)
        seen.save(now=now)
        print(f"poll: PRIMED {len(new)} postings")
        return

    survivors = [p for p in new if passes_rules(p, PROFILE, now)]
    survivors = await enrich_postings(survivors, CMAP)
    scored = await _score_all(PROVIDER, survivors, PROFILE)
    existing = set(await asyncio.to_thread(sheet.existing_uids, WS))

    added = pinged = 0
    for p, score in scored:
        company = CMAP.get((p.ats, p.company))
        level = classify(p, score, company, PROFILE, now)
        if level is None:
            continue
        if p.uid not in existing:
            await asyncio.to_thread(sheet.append_match, WS, p, score)
            added += 1
        if level in PING_LEVELS or (company and company.tier == "dream"):
            await _post_ping(p, score, level, company)
            pinged += 1

    for p in postings:
        seen.mark(p, now)
    seen.save(now=now)
    print(f"poll: {len(postings)} fetched, {len(new)} new, {len(survivors)} survivors, "
          f"+{added} to sheet, {pinged} pinged, {len(errors)} errors")


@tasks.loop(hours=24)
async def reminder_loop():
    records = await asyncio.to_thread(sheet.all_records, WS)
    today = date.today()
    due = tracker.due_soon(records, today, within_days=3)
    nudge = tracker.unapplied_strong(records, today, min_fit=80, older_than_days=3)
    if not due and not nudge:
        return
    parts = []
    if due:
        parts.append("**Due soon:**\n" + "\n".join(
            f"- {r.get('Role', '?')} @ {r.get('Company', '?')} (due {r.get('Deadline')})" for r in due[:10]))
    if nudge:
        parts.append(f"**{len(nudge)} strong roles you haven't applied to yet** (try `/top`).")
    channel = bot.get_channel(CHANNEL_ID) or await bot.fetch_channel(CHANNEL_ID)
    await channel.send(embed=discord.Embed(title="Reminders", description="\n\n".join(parts)[:4000],
                                           color=0xE67E22))


@poll_loop.before_loop
@reminder_loop.before_loop
async def _before():
    await bot.wait_until_ready()


# --- slash commands ---
async def _records():
    return await asyncio.to_thread(sheet.all_records, WS)


@tree.command(name="pending", description="Your unapplied matches, best fit first")
async def pending(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_list_embed("Pending", tracker.top_unapplied(await _records(), 15)))


@tree.command(name="top", description="Your best N unapplied matches right now")
async def top(interaction: discord.Interaction, n: int = 5):
    await interaction.response.send_message(embed=_list_embed(f"Top {n}", tracker.top_unapplied(await _records(), n)))


@tree.command(name="due", description="Roles with a deadline in the next 7 days")
async def due(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_list_embed("Due this week", tracker.due_soon(await _records(), date.today(), within_days=7)))


@tree.command(name="stats", description="Pipeline counts by status")
async def stats(interaction: discord.Interaction):
    s = tracker.stats(await _records())
    desc = "\n".join(f"- {k}: {v}" for k, v in sorted(s.items())) or "Empty so far."
    await interaction.response.send_message(embed=discord.Embed(title="Pipeline", description=desc, color=0x3498DB))


async def _setup():
    bot.add_dynamic_items(TrackerButton)
    await tree.sync()
    poll_loop.start()
    reminder_loop.start()


def main():
    from dotenv import load_dotenv
    load_dotenv()

    global WS, PROFILE, COMPANIES, CMAP, PROVIDER, CHANNEL_ID, SEEN_PATH
    token = os.environ["DISCORD_BOT_TOKEN"]
    CHANNEL_ID = int(os.environ["DISCORD_CHANNEL_ID"])
    SEEN_PATH = os.environ.get("SEEN_PATH", ".state/seen.json")

    PROFILE = load_profile(os.environ.get("PROFILE_PATH", "config/profile.yaml"))
    COMPANIES = load_companies(os.environ.get("COMPANIES_PATH", "config/companies.yaml"))
    CMAP = _company_map(COMPANIES)

    class _S:  # minimal settings shim for build_provider
        llm_api_key = os.environ.get("LLM_API_KEY")
        llm_provider = (os.environ.get("LLM_PROVIDER") or "gemini").lower()
        llm_model = os.environ.get("LLM_MODEL", "")
    PROVIDER = build_provider(_S())

    WS = sheet.connect(os.environ["GOOGLE_CREDENTIALS_PATH"], os.environ["GOOGLE_SHEET_ID"])

    bot.setup_hook = _setup
    bot.run(token)


if __name__ == "__main__":
    main()
