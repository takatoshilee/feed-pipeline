"""Confirm the Discord webhook works. Run from the repo with the venv active:
    python scripts/test_ping.py "<webhook_url>"
or set DISCORD_WEBHOOK_URL and run with no argument.
Prints a clear result in every case."""
import os
import sys

import httpx

url = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DISCORD_WEBHOOK_URL", "")
if not url.startswith("http"):
    print("No webhook URL. Pass it as an argument: python scripts/test_ping.py \"https://discord.com/api/webhooks/...\"")
    sys.exit(1)

print(f"Posting to {url[:45]}...")
try:
    r = httpx.post(url, json={"content": "job-radar test ping"}, timeout=15)
    if r.status_code == 204:
        print("SUCCESS (204): check your Discord channel for 'job-radar test ping'")
    else:
        print(f"FAILED: HTTP {r.status_code} -> {r.text[:200]}")
except Exception as e:
    print(f"ERROR: {e!r}")
