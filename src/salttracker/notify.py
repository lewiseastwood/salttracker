"""Notify a person about Pennsylvania RTK send/response.

Weekly dashboard refresh does not use this. A missing webhook or mailbox is a
no-op so drafts can exist before Lewis has given a reply-to address.
"""
from __future__ import annotations

import json
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

from .sources import UA


FAIL_KINDS = frozenset({"parse-failed", "listing-empty", "listing-unfetched",
                        "download-failed"})


def channel_configured() -> bool:
    hook = os.environ.get("SALTTRACKER_WEBHOOK_URL", "").strip()
    email = os.environ.get("SALTTRACKER_ALERT_EMAIL", "").strip()
    host = os.environ.get("SALTTRACKER_SMTP_HOST", "").strip()
    return bool(hook or (email and host))


def alert_body(alerts: list[dict]) -> str:
    failed = any(a.get("kind") in FAIL_KINDS for a in alerts)
    header = ("SaltTracker refresh failed:" if failed
              else "New road-salt contract data was published:")
    lines = [header, ""]
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


def send_email(alerts_text: str, count: int, failed: bool = False) -> str:
    to = os.environ.get("SALTTRACKER_ALERT_EMAIL", "").strip()
    host = os.environ.get("SALTTRACKER_SMTP_HOST", "").strip()
    if not (to and host):
        raise RuntimeError("SALTTRACKER_ALERT_EMAIL and SALTTRACKER_SMTP_HOST required")
    msg = EmailMessage()
    msg["Subject"] = (
        f"SaltTracker: refresh failed ({count} signal(s))"
        if failed else f"SaltTracker: {count} new contract signal(s)"
    )
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


def followup_event(kind: str, text: str) -> list[str]:
    """Ping that an RTK was sent or a written response was recorded.

    No-op if no channel is configured. Does not send the government letters.
    """
    if not channel_configured():
        return []
    subject = (
        "SaltTracker: RTK sent to government"
        if kind == "sent"
        else "SaltTracker: government RTK response recorded"
    )
    payload = {"ts": "", "source": "salttracker", "kind": f"followup-{kind}", "text": text}
    notes: list[str] = []
    hook = os.environ.get("SALTTRACKER_WEBHOOK_URL", "").strip()
    if hook:
        try:
            notes.append(post_webhook(hook, payload))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            notes.append(f"webhook failed: {exc}")
    if os.environ.get("SALTTRACKER_ALERT_EMAIL") and os.environ.get("SALTTRACKER_SMTP_HOST"):
        try:
            notes.append(_email_followup(subject, text))
        except (OSError, smtplib.SMTPException, RuntimeError) as exc:
            notes.append(f"email failed: {exc}")
    return notes


def _email_followup(subject: str, body: str) -> str:
    to = os.environ.get("SALTTRACKER_ALERT_EMAIL", "").strip()
    host = os.environ.get("SALTTRACKER_SMTP_HOST", "").strip()
    if not (to and host):
        raise RuntimeError("SALTTRACKER_ALERT_EMAIL and SALTTRACKER_SMTP_HOST required")
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SALTTRACKER_SMTP_FROM", to)
    msg["To"] = to
    msg.set_content(body)
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
    """Local refresh.py alerts. Missing config is a no-op. Not used weekly."""
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
            notes.append(send_email(
                payload["text"], len(alerts),
                failed=any(a.get("kind") in FAIL_KINDS for a in alerts),
            ))
        except (OSError, smtplib.SMTPException, RuntimeError) as exc:
            notes.append(f"email failed: {exc}")
    return notes
