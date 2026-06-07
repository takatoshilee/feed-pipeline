"""Daily reminder pass for the cron+Sheet setup. Reads the tracker Sheet and pings the
Discord webhook about deadlines coming up and strong matches still unapplied. Repeats
daily by design: a reminder stops only once you mark the row Applied/Skip in the Sheet.
Run: python -m job_radar.remind  (needs DISCORD_WEBHOOK_URL + GOOGLE_SHEET_ID + creds)."""
import asyncio
from datetime import date

from . import sheet, tracker
from .config import load_settings
from .notify import ConsoleNotifier, DiscordNotifier


def build_message(records, today: date) -> str | None:
    """Compose the reminder body from the Sheet rows, or None if there's nothing to nag
    about. 'Due soon' = pending with a deadline within 3 days; 'strong, not applied' =
    pending, fit >= 80, sitting for a few days."""
    due = tracker.due_soon(records, today, within_days=3)
    nudge = tracker.unapplied_strong(records, today, min_fit=80, older_than_days=3)
    if not due and not nudge:
        return None

    parts = []
    if due:
        lines = "\n".join(
            f"- {r.get('Role', '?')} @ {r.get('Company', '?')} (due {r.get('Deadline')})"
            for r in due[:10])
        parts.append("**Due soon**\n" + lines)
    if nudge:
        lines = "\n".join(
            f"- {r.get('Role', '?')} @ {r.get('Company', '?')} ({tracker.fit(r)}/100)"
            for r in nudge[:10])
        more = f"\n...and {len(nudge) - 10} more" if len(nudge) > 10 else ""
        parts.append(f"**Strong, not applied yet ({len(nudge)})**\n" + lines + more)
    return "\n\n".join(parts)


async def remind(ws, notifier, today: date | None = None) -> int:
    today = today or date.today()
    records = await asyncio.to_thread(sheet.all_records, ws)
    body = build_message(records, today)
    if body is None:
        print("remind: nothing due or pending")
        return 0
    await notifier.send_embed("Job tracker reminders", body)
    print("remind: sent reminder")
    return 1


def main():
    settings = load_settings()
    if not (settings.sheet_id and settings.creds_path):
        raise SystemExit("remind: set GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_PATH")
    ws = sheet.connect(settings.creds_path, settings.sheet_id)
    notifier = (DiscordNotifier(settings.webhook_url) if settings.webhook_url
                else ConsoleNotifier())
    asyncio.run(remind(ws, notifier))


if __name__ == "__main__":
    main()
