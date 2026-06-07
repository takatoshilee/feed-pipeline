from datetime import date

from job_radar.remind import build_message, remind


def _row(uid, fit, status="New", deadline="", posted=""):
    return {"uid": uid, "Company": "Acme", "Role": f"Role {uid}", "Fit": str(fit),
            "Status": status, "Deadline": deadline, "Posted": posted}


TODAY = date(2026, 6, 7)


def test_build_message_none_when_nothing_pending():
    rows = [_row("1", 90, status="Applied", deadline="2026-06-08")]  # applied -> no nag
    assert build_message(rows, TODAY) is None


def test_build_message_flags_due_and_strong():
    rows = [
        _row("due", 60, deadline="2026-06-09"),                 # deadline in 2 days -> due soon
        _row("strong", 88, posted="2026-06-01"),               # fit 88, 6 days old -> nudge
        _row("fresh", 95, posted="2026-06-07"),                # too fresh to nag (today)
        _row("applied", 99, status="Applied", deadline="2026-06-08"),  # ignored
    ]
    msg = build_message(rows, TODAY)
    assert "Due soon" in msg and "Role due" in msg
    assert "Strong, not applied yet (1)" in msg and "Role strong" in msg
    assert "Role fresh" not in msg and "Role applied" not in msg


class FakeWS:
    def __init__(self, records):
        self._records = records

    def get_all_records(self):
        return self._records


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send_embed(self, title, description, color=0):
        self.sent.append((title, description))


async def test_remind_sends_when_due():
    ws = FakeWS([_row("due", 70, deadline="2026-06-08")])
    notifier = FakeNotifier()
    sent = await remind(ws, notifier, today=TODAY)
    assert sent == 1 and len(notifier.sent) == 1
    assert "Due soon" in notifier.sent[0][1]


async def test_remind_silent_when_empty():
    ws = FakeWS([_row("done", 90, status="Applied")])
    notifier = FakeNotifier()
    sent = await remind(ws, notifier, today=TODAY)
    assert sent == 0 and notifier.sent == []
