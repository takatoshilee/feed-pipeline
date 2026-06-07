from datetime import date

from job_radar.remind import build_message, remind


def _row(uid, fit, status="New", deadline="", posted="", priority="", applied_on=""):
    return {"uid": uid, "Company": "Acme", "Role": f"Role {uid}", "Fit": str(fit),
            "Status": status, "Deadline": deadline, "Posted": posted, "Priority": priority,
            "Applied on": applied_on}


TODAY = date(2026, 6, 7)


def test_build_message_none_when_nothing_pending():
    rows = [_row("1", 90, status="Applied", deadline="2026-06-08")]  # applied -> no nag
    assert build_message(rows, TODAY) is None


def test_build_message_flags_due_and_top_pending():
    rows = [
        _row("due", 60, deadline="2026-06-09"),                 # deadline in 2 days -> due soon
        _row("strong", 88, posted="2026-06-01"),               # high fit -> top pending
        _row("applied", 99, status="Applied", deadline="2026-06-08"),  # applied -> excluded
    ]
    msg = build_message(rows, TODAY)
    assert "Due soon" in msg and "Role due" in msg
    assert "Top pending right now" in msg and "Role strong" in msg
    assert "Role applied" not in msg            # applied rows never resurface


def test_build_message_includes_link_and_catchup_header():
    rows = [
        _row("must", 55, priority="must"),                     # flagged -> must-apply, any fit
        _row("a", 90),
        _row("done", 70, status="Applied", applied_on="2026-06-02"),  # sets last-active
    ]
    msg = build_message(rows, TODAY, sheet_url="https://sheet/x")
    assert "Must apply (your priority)" in msg and "Role must" in msg
    assert "last applied 5d ago" in msg          # TODAY - 2026-06-02 = 5 days
    assert "[Open your tracker →](https://sheet/x)" in msg
    # must-apply section comes before the general top-pending list
    assert msg.index("Must apply") < msg.index("Top pending")


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
