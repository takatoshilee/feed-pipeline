"""Daily reminder pass for the cron+Sheet setup. Reads the tracker Sheet and pings the
Discord webhook about deadlines coming up and strong matches still unapplied. Repeats
daily by design: a reminder stops only once you mark the row Applied/Skip in the Sheet.
Run: python -m job_radar.remind  (needs DISCORD_WEBHOOK_URL + GOOGLE_SHEET_ID + creds)."""
import asyncio
from datetime import date

from . import sheet, tracker
from .config import load_settings
from .notify import ConsoleNotifier, DiscordNotifier


def _fmt(rows, withfit=True):
    def line(r):
        tail = f" ({tracker.fit(r)}/100)" if withfit else ""
        due = f" · due {r.get('Deadline')}" if r.get("Deadline") else ""
        return f"- {r.get('Role', '?')} @ {r.get('Company', '?')}{tail}{due}"
    return "\n".join(line(r) for r in rows)


def build_message(records, today: date, sheet_url: str | None = None) -> str | None:
    """Compose the reminder body from the Sheet rows, or None if nothing's pending. Leads
    with a catch-up header (how many pending, days since Taka last applied) so a busy
    stretch surfaces the backlog, then must-apply (his priority flags) -> due-soon
    (deadline within 3 days) -> top pending right now (best by fit), then a link to the
    full Sheet. The top-pending section means the ping always shows actionable roles, not
    just an empty 'nothing urgent'."""
    must = tracker.must_apply(records)
    due = tracker.due_soon(records, today, within_days=3)
    top = tracker.top_unapplied(records, 10)
    if not (must or due or top):
        return None

    parts = []
    pending = tracker.pending_count(records)
    fresh = len(tracker.recently_posted(records, today, within_days=7))
    la = tracker.last_active(records)
    fresh_bit = f" · {fresh} new this week" if fresh else ""
    if la is not None:
        parts.append(f"_{pending} pending{fresh_bit} · last applied {(today - la).days}d ago_")
    elif pending:
        parts.append(f"_{pending} pending{fresh_bit} · nothing applied yet_")

    if must:
        parts.append("**Must apply (your priority)**\n" + _fmt(must[:10]))
    if due:
        parts.append("**Due soon**\n" + _fmt(due[:10], withfit=False))
    if top:
        extra = f"\n...and {pending - len(top)} more in the sheet" if pending > len(top) else ""
        parts.append("**Top pending right now**\n" + _fmt(top) + extra)
    if sheet_url:
        parts.append(f"[Open your tracker →]({sheet_url})")
    return "\n\n".join(parts)


async def remind(ws, notifier, today: date | None = None, sheet_url: str | None = None) -> int:
    today = today or date.today()
    records = await asyncio.to_thread(sheet.all_records, ws)
    body = build_message(records, today, sheet_url=sheet_url)
    if body is None:
        print("remind: nothing due or pending")
        return 0
    await notifier.send_embed("Job tracker", body)
    print("remind: sent reminder")
    return 1


def _sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


def main():
    settings = load_settings()
    if not (settings.sheet_id and settings.creds_path):
        raise SystemExit("remind: set GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_PATH")
    ws = sheet.connect(settings.creds_path, settings.sheet_id)
    notifier = (DiscordNotifier(settings.webhook_url) if settings.webhook_url
                else ConsoleNotifier())
    asyncio.run(remind(ws, notifier, sheet_url=_sheet_url(settings.sheet_id)))


if __name__ == "__main__":
    main()
