"""Send refresh alerts off the machine.

A scheduled run that only appends to alerts.jsonl is silent if nobody opens the
folder. A webhook (Slack incoming, Teams, generic POST) or an SMTP mailbox is
what makes a new FY2028 contract visible in June.
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from .sources import UA


def alert_body(alerts: list[dict]) -> str:
    lines = ["New road-salt contract data was published:", ""]
    for a in alerts:
        lines.append(f"- [{a['kind']}] {a['detail']}")
    lines.append("")
    lines.append("Re-run scripts/refresh.py and open the dashboard to review.")
    return "\n".join(lines)


def post_webhook(url: str, payload: dict, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return f"webhook {resp.status}"


def send_email(alerts_text: str, count: int) -> str:
    to = os.environ.get("SALTTRACKER_ALERT_EMAIL", "").strip()
    host = os.environ.get("SALTTRACKER_SMTP_HOST", "").strip()
    if not (to and host):
        raise RuntimeError("SALTTRACKER_ALERT_EMAIL and SALTTRACKER_SMTP_HOST required")
    msg = EmailMessage()
    msg["Subject"] = f"SaltTracker: {count} new contract signal(s)"
    msg["From"] = os.environ.get("SALTTRACKER_SMTP_FROM", to)
    msg["To"] = to
    msg.set_content(alerts_text)
    port = int(os.environ.get("SALTTRACKER_SMTP_PORT", "587"))
    user = os.environ.get("SALTTRACKER_SMTP_USER", "")
    password = os.environ.get("SALTTRACKER_SMTP_PASS", "")
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.send_message(msg)
    return f"emailed {to}"


def dispatch(alerts: list[dict], stamp: str) -> list[str]:
    """Fire configured notifiers. Missing config is a no-op, not an error."""
    if not alerts:
        return []
    payload = {"ts": stamp, "source": "salttracker", "alerts": alerts,
               "text": alert_body(alerts)}
    notes: list[str] = []
    hook = os.environ.get("SALTTRACKER_WEBHOOK_URL", "").strip()
    if hook:
        try:
            notes.append(post_webhook(hook, payload))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            notes.append(f"webhook failed: {exc}")
    if os.environ.get("SALTTRACKER_ALERT_EMAIL") and os.environ.get("SALTTRACKER_SMTP_HOST"):
        try:
            notes.append(send_email(payload["text"], len(alerts)))
        except (OSError, smtplib.SMTPException, RuntimeError) as exc:
            notes.append(f"email failed: {exc}")
    return notes
