#!/usr/bin/env python3
"""Pennsylvania follow-up: contacts workbook and Right-to-Know drafts.

County letters ask that county's own COSTARS and off-contract salt purchases
for FY2022–FY2027 — not municipal purchases. The DGS letter asks for supplier
weekly shipment reports, missing award/price files (FY2022–FY2023), missing
statewide estimates (FY2023 beyond the eight-county re-bid; FY2025 renewal),
and monthly COSTARS sales summaries for those seasons.

Default is draft-only. This script does not send mail unless you pass --send
and set SALTTRACKER_FOLLOWUP_CONFIRM=YES. Drafts are refused if
SALTTRACKER_FOLLOWUP_REPLY_TO or SALTTRACKER_FOLLOWUP_ADDRESS is unset: the RTKL
requires a name and a verifiable address.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import smtplib
import sys
from email.message import EmailMessage
from email.parser import BytesParser
from email.policy import default as email_policy
from email.utils import formatdate
from pathlib import Path

import requests
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pypdf import PdfReader, PdfWriter
from pypdf.generic import BooleanObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
CONTACTS = ROOT / "data" / "pa_followup" / "contacts.csv"
OUT = ROOT / "data" / "output" / "pa_followup"
DRAFTS = OUT / "drafts"

CONTRACT_NO = "6100065611"  # current FY2027 COSTARS solicitation; earlier seasons named in the letters
SEASONS = "FY2022 through FY2027 (winters 2021–22 through 2026–27)"

SUBJECT = (
    f"Right-to-Know request: {SEASONS} COSTARS sodium chloride purchases"
)

COUNTY_BODY = """{greeting}

I am requesting public records under Pennsylvania's Right-to-Know Law (65 P.S. § 67.101 et seq.).

Please provide, for {county} County only, for each of FY2022, FY2023, FY2024, FY2025, FY2026 and FY2027 (winter seasons 2021–22 through 2026–27):

1. The county's own sodium chloride (bulk road salt) purchases under Commonwealth COSTARS: tons committed, tons actually received, unit prices paid, and invoices or delivery records if held. Known statewide solicitations in that span include 6100053321, 6100056192, 6100063746 and 6100065611; please include the same records for any other COSTARS sodium chloride contract the county used in those years, including the 2023–24 packet and the 2024–25 renewal.
2. Any separate sodium chloride / road-salt purchase by {county} County that was not made under COSTARS, for the same years: tons, prices, and invoices if held.

I am not seeking bid bonds, sealed proposals that remain unopened, records of municipalities or other COSTARS members, or any record that is not public. Electronic copies (PDF) are preferred.

Please send records or a response to:

{sender_name}
{sender_address}
{reply_to}

Thank you for your time.

{sender_name}
"""

DGS_SUBJECT = (
    f"Right-to-Know request: COSTARS sodium chloride records, {SEASONS}"
)

DGS_BODY = """{greeting}

I am requesting public records under Pennsylvania's Right-to-Know Law (65 P.S. § 67.101 et seq.).

Please provide the following public records for Commonwealth COSTARS sodium chloride (bulk road salt) for FY2022 through FY2027 (winter seasons 2021–22 through 2026–27). Known solicitations include 6100053321 (FY2022), 6100056192 (FY2023 re-bid), 6100063746 (FY2026) and 6100065611 (FY2027). Please include the 2023–24 COSTARS season contract and the 2024–25 renewal even if they were not posted under a new solicitation id.

1. Weekly shipment reports that awarded suppliers filed with DGS for those seasons, by COSTARS member and by PennDOT / non-PennDOT agency, including awarded tons and tons shipped (and tons shipped to date, if that is how the reports are kept). Monthly COSTARS sales summaries for the same seasons, if held separately from the weekly files.

2. Award notices, county bid-price awards, and change notices that state the awarded supplier and delivered price by county for FY2022 and FY2023. Those award files are not in the public COSTARS packets this requester holds.

3. Estimated requirements / county-lot tonnage for FY2025. The 2024–25 season was a renewal and no estimates attachment was published. If a statewide tonnage table exists in any form, please provide it.

4. Any statewide estimated-requirements table for FY2023 covering all 67 counties. The public 2022–23 re-bid estimates file (6100056192) lists eight counties only.

If per-supplier shipment volumes in item 1 are withheld as confidential proprietary information, or if suppliers are given notice to object to release, I will accept as an alternative the same figures aggregated by COSTARS member and by agency without supplier attribution: tons awarded and tons shipped, by member and by agency, for each of those seasons. I am requesting that narrower alternative now so a third-party notice process need not result in a full denial and a second request.

I am not seeking bid bonds, sealed proposals that remain unopened, or any record that is not public. Electronic copies (Excel or PDF) are preferred. A completed DGS standard RTKL request form is attached.

Please send records or a response to:

{sender_name}
{sender_address}
{reply_to}

Thank you for your time.

{sender_name}
"""

# Published DGS RTK intake (retrieved 10 Sep 2026). Not the commodity specialist.
DGS_AORO = {
    "officer": "L. Paul Vezzetti",
    "title": "Agency Open Records Officer",
    "attn": "Cheryl Spackman, Right-To-Know Law (RTKL) Coordinator",
    "email": "DGS-RTK@pa.gov",
    "address": "Department of General Services, 603 North Office Building, Harrisburg, PA 17125",
    "source_url": (
        "https://www.pa.gov/services/dgs/submit-a-right-to-know-request-"
        "to-the-pennsylvania-department-of-general-services"
    ),
}

DGS_FORM_URL = (
    "https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/"
    "documents/press-office/rtkrequestform.pdf"
)
DGS_FORM_FILENAME = "DGS_RTKL_request_COSTARS_salt_FY2022-FY2027.pdf"

# Commonwealth administrative-office holidays. The named days come from
# Governor's Office Administrative Circular 25-13 (Holidays — 2026),
# 19 Aug 2025, https://www.pa.gov/content/dam/copapwp-pagov/en/oa/documents/policies/ac/25-13.pdf
# (also AC 24-12 for 2025). Those circulars close state offices on New Year's
# Day, Dr. Martin Luther King Jr. Day, Presidents' Day, Memorial Day,
# Juneteenth National Freedom Day, Independence Day, Labor Day, Indigenous
# People's Day, Veterans Day, Thanksgiving, the day after Thanksgiving, and
# Christmas. Weekend observances follow the Friday-before / Monday-after
# rule the circulars actually use (Independence Day 2026 is Friday 3 July
# because 4 July is a Saturday). Counties may close on a different calendar;
# this list is the one that governs DGS.
RESPONSE_BUSINESS_DAYS = 5
APPEAL_BUSINESS_DAYS = 15


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> dt.date:
    if month == 12:
        d = dt.date(year, 12, 31)
    else:
        d = dt.date(year, month + 1, 1) - dt.timedelta(days=1)
    d -= dt.timedelta(days=(d.weekday() - weekday) % 7)
    return d


def _observed(day: dt.date) -> dt.date:
    if day.weekday() == 5:
        return day - dt.timedelta(days=1)
    if day.weekday() == 6:
        return day + dt.timedelta(days=1)
    return day


def commonwealth_holidays(year: int) -> set[dt.date]:
    """Closed dates for Commonwealth administrative offices in `year`."""
    return {
        _observed(dt.date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _last_weekday(year, 5, 0),
        _observed(dt.date(year, 6, 19)),
        _observed(dt.date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 10, 0, 2),
        _observed(dt.date(year, 11, 11)),
        _nth_weekday(year, 11, 3, 4),
        _nth_weekday(year, 11, 3, 4) + dt.timedelta(days=1),
        _observed(dt.date(year, 12, 25)),
    }


def is_commonwealth_business_day(day: dt.date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in commonwealth_holidays(day.year)


def add_business_days(start: dt.date, n: int) -> dt.date:
    """Advance `n` Commonwealth business days after `start` (start not counted).

    Management Directive 205.36 Amended: the day a RTKL request is received
    (or deemed received) is not counted; the first day of the five-business-day
    period is the agency's next business day.
    https://www.pa.gov/content/dam/copapwp-pagov/en/dgs/documents/documents/press-office/205_36.pdf
    """
    d = start
    left = n
    while left:
        d += dt.timedelta(days=1)
        if is_commonwealth_business_day(d):
            left -= 1
    return d


def next_business_day_on_or_after(day: dt.date) -> dt.date:
    d = day
    while not is_commonwealth_business_day(d):
        d += dt.timedelta(days=1)
    return d


def business_days_between(later_exclusive_start: dt.date, end: dt.date) -> int:
    """Business days strictly after `later_exclusive_start` through `end` inclusive."""
    if end <= later_exclusive_start:
        return 0
    n = 0
    d = later_exclusive_start
    while d < end:
        d += dt.timedelta(days=1)
        if is_commonwealth_business_day(d):
            n += 1
    return n


def parse_iso_date(value: str) -> dt.date | None:
    text = (value or "").strip()
    if not text:
        return None
    return dt.date.fromisoformat(text)


def clock_row(row: dict, today: dt.date) -> dict:
    """Per-request RTKL clock. Receipt by the AORO starts it, not send date."""
    label = row.get("county") or "?"
    received_raw = parse_iso_date(row.get("received_on") or "")
    responded = parse_iso_date(row.get("responded_on") or "")
    result = {
        "label": label,
        "received_on": None,
        "received_note": "",
        "response_due": None,
        "remaining": "—",
        "status": "not received",
        "overdue": False,
        "appeal_by": None,
        "appeal_remaining": "",
        "flag": "",
    }
    if received_raw is None:
        return result
    received = next_business_day_on_or_after(received_raw)
    result["received_on"] = received
    if received != received_raw:
        result["received_note"] = (
            f"entered {received_raw.isoformat()}; treated as received {received.isoformat()} "
            "(weekend/holiday → next business day)"
        )
    due = add_business_days(received, RESPONSE_BUSINESS_DAYS)
    result["response_due"] = due
    if responded is not None:
        result["status"] = "responded"
        result["remaining"] = "—"
        return result
    if today <= due:
        left = business_days_between(today, due)
        result["status"] = "awaiting"
        result["remaining"] = "due today" if left == 0 else f"{left} business day" + ("s" if left != 1 else "")
        return result
    appeal_by = add_business_days(due, APPEAL_BUSINESS_DAYS)
    result["overdue"] = True
    result["appeal_by"] = appeal_by
    past = (today - due).days
    result["remaining"] = f"{past} calendar day" + ("s" if past != 1 else "") + " past due"
    if today <= appeal_by:
        appeal_left = business_days_between(today, appeal_by)
        result["status"] = "OVERDUE"
        result["flag"] = "OVERDUE — deemed denial; appeal window open"
        result["appeal_remaining"] = (
            "due today" if appeal_left == 0
            else f"{appeal_left} business day" + ("s" if appeal_left != 1 else "") + " left"
        )
    else:
        result["status"] = "OVERDUE"
        result["flag"] = "OVERDUE — appeal window closed"
        result["appeal_remaining"] = "closed"
    return result


def format_clock_report(rows: list[dict], today: dt.date) -> str:
    lines = [
        f"PA RTKL clock  today {today.isoformat()}",
        "The five-business-day period starts when the AORO receives the request, not when you send it",
        "(65 P.S. § 67.901; Commonwealth v. Donahue, 98 A.3d 1223 (Pa. 2014)).",
        "Email after regular business hours is received the next business day (MD 205.36 Amended;",
        "OOR AORO Guidebook). Fill received_on with that receipt date. Day of receipt is not counted.",
        "Holidays: Commonwealth administrative offices (AC 25-13, 2026).",
        "A missed deadline is a deemed denial; appeal by 15 business days after that date (§ 67.1101).",
        "",
    ]
    any_overdue = False
    for row in rows:
        info = clock_row(row, today)
        head = info["label"]
        if info["overdue"]:
            any_overdue = True
            head = f"{head}  {info['flag']}"
        rec = info["received_on"].isoformat() if info["received_on"] else "—"
        due = info["response_due"].isoformat() if info["response_due"] else "—"
        lines.append(head)
        lines.append(f"  received_on     {rec}")
        if info["received_note"]:
            lines.append(f"                  {info['received_note']}")
        lines.append(f"  response_due    {due}")
        lines.append(f"  remaining       {info['remaining']}")
        lines.append(f"  status          {info['status']}")
        if info["appeal_by"] is not None:
            lines.append(
                f"  appeal_by       {info['appeal_by'].isoformat()}  "
                f"(15 business days from deemed denial; {info['appeal_remaining']})"
            )
        lines.append("")
    if any_overdue:
        lines.append("Flagged rows are overdue. A deemed denial starts the 15-business-day appeal window.")
    else:
        lines.append("No overdue rows.")
    return "\n".join(lines).rstrip() + "\n"


class SenderConfigError(RuntimeError):
    """Drafts cannot be written without a reply-to and a postal address."""


def load_local_env(path: Path | None = None) -> None:
    """Load ROOT/.env into os.environ without overriding values already set.

    The file is gitignored. Shell exports win. Values are never printed.
    """
    env_path = path or (ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def is_dgs_row(row: dict) -> bool:
    return (row.get("county") or "").strip().upper() == "DGS"


def parse_mailing_address(address: str) -> tuple[str, str, str, str]:
    """Split a one-line US address into street, city, state, zip.

    If the line does not match 'street, city, ST ZIP', the whole string is
    the street and the other parts are empty.
    """
    text = " ".join((address or "").split())
    match = re.search(
        r"^(.*?),\s*([^,]+),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$",
        text,
    )
    if match:
        return match.group(1), match.group(2), match.group(3).upper(), match.group(4)
    return text, "", "", ""


def dgs_greeting() -> str:
    return (
        f"Dear {DGS_AORO['officer']}, {DGS_AORO['title']}:\n\n"
        f"Attn: {DGS_AORO['attn']}"
    )


def dgs_letter_text(reply: str, address: str, name: str) -> str:
    return DGS_BODY.format(
        greeting=dgs_greeting(),
        sender_name=name,
        sender_address=address,
        reply_to=reply,
    )


def dgs_records_requested() -> tuple[str, str]:
    """Primary and continuation text for the DGS standard RTKL form."""
    primary = (
        "COSTARS sodium chloride (bulk road salt) records for FY2022–FY2027 "
        "(winters 2021–22 through 2026–27), including solicitations 6100053321, "
        "6100056192, 6100063746, 6100065611, the 2023–24 packet, and the 2024–25 "
        "renewal: (1) weekly supplier shipment reports and monthly COSTARS sales "
        "summaries by COSTARS member and by PennDOT / non-PennDOT agency "
        "(awarded tons and tons shipped); (2) FY2022 and FY2023 award notices / "
        "county bid prices / change notices."
    )
    continuation = (
        "(3) FY2025 estimated requirements / county-lot tonnage (renewal year; "
        "none published). (4) FY2023 statewide estimates for all 67 counties "
        "(public re-bid 6100056192 lists eight counties). Fallback for item 1: "
        "aggregate tons awarded and shipped by member and by agency without "
        "supplier attribution. Electronic copies preferred. Not bid bonds, "
        "sealed unopened proposals, or non-public records."
    )
    return primary, continuation


def dgs_form_paste_block(reply: str, address: str, name: str) -> str:
    street, city, state, zip_code = parse_mailing_address(address)
    primary, continuation = dgs_records_requested()
    return (
        "DGS STANDARD RTKL FORM — PASTE BLOCK\n"
        "Source: " + DGS_FORM_URL + "\n"
        "SUBMITTED TO AGENCY NAME: Pennsylvania Department of General Services "
        "(Attn: AORO)\n"
        "Date Request Submitted: (fill on the day you send)\n"
        "Submitted via: Email\n"
        f"Full Name: {name}\n"
        "Company: (leave blank)\n"
        "Please send response via: Email\n"
        f"Email: {reply}\n"
        f"Mailing Address: {street}\n"
        f"City: {city}    State: {state}    Zip: {zip_code}\n"
        "Telephone: (leave blank unless you want a call)\n"
        "How do you prefer to be contacted: Email\n"
        "Affirm US resident / true contact info: checked\n"
        f"RECORDS REQUESTED (page 1): {primary}\n"
        f"RECORDS REQUESTED (continued): {continuation}\n"
        "DO YOU WANT COPIES?: Yes, electronic\n"
        "Notify me if fees will be more than: $100\n"
        "Certified copies: No\n"
    )


def fetch_dgs_rtkl_form() -> bytes:
    response = requests.get(
        DGS_FORM_URL,
        timeout=30,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
            "Accept": "application/pdf,*/*",
        },
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise RuntimeError("DGS RTKL form URL did not return a PDF")
    return response.content


def dgs_form_values(reply: str, address: str, name: str) -> dict[str, str]:
    street, city, state, zip_code = parse_mailing_address(address)
    primary, continuation = dgs_records_requested()
    checked = "/On"
    return {
        "SUBMITTED TO AGENCY NAME": (
            "Pennsylvania Department of General Services (Attn: AORO)"
        ),
        "Full Name": name,
        "Email_3": reply,
        "Mailing Address": street,
        "City": city,
        "State": state,
        "Zip": zip_code,
        "Records Requested1": primary,
        "Records Requested2": continuation,
        "Email": checked,
        "Email_2": checked,
        "Email_4": checked,
        "Yes electronic": checked,
        "No": checked,
        "100 or": checked,
        "By checking this box I affirm that my full name and contact information is true and correct": checked,
    }


def fill_dgs_rtkl_form(blank: bytes, reply: str, address: str, name: str) -> bytes:
    reader = PdfReader(io.BytesIO(blank))
    writer = PdfWriter()
    writer.append(reader)
    values = dgs_form_values(reply, address, name)
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=True)
    if "/AcroForm" in writer._root_object:
        writer._root_object["/AcroForm"].update(
            {NameObject("/NeedAppearances"): BooleanObject(True)}
        )
    out = io.BytesIO()
    writer.write(out)
    filled = out.getvalue()
    if not filled.startswith(b"%PDF"):
        raise RuntimeError("filled DGS form is not a PDF")
    return filled


def build_dgs_form_attachment(reply: str, address: str, name: str) -> tuple[bytes | None, str]:
    """Fetch and fill the DGS standard form.

    Returns (pdf_bytes or None, status) where status is 'filled', 'blank',
    or 'unavailable'. 'blank' means the official form is attached unfilled
    and the email should include the paste block.
    """
    try:
        blank = fetch_dgs_rtkl_form()
    except Exception:
        return None, "unavailable"
    try:
        return fill_dgs_rtkl_form(blank, reply, address, name), "filled"
    except Exception:
        return blank, "blank"


def message_plain_text(raw: bytes | EmailMessage) -> str:
    msg = raw if isinstance(raw, EmailMessage) else BytesParser(policy=email_policy).parsebytes(raw)
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                return part.get_content()
    return msg.get_content()


def load_contacts(path: Path = CONTACTS) -> list[dict]:
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def greeting_line(row: dict) -> str:
    officer = (row.get("aoro_officer") or "").strip()
    role = (row.get("aoro_title") or "Agency Open Records Officer").strip()
    county = row.get("county") or "the county"
    if officer:
        return f"Dear {officer}, {role}, {county} County:"
    return f"Dear {role}, {county} County:"


def sender_fields() -> tuple[str, str, str]:
    """Reply-to, postal address, and name. Address and reply-to are required."""
    reply = os.environ.get("SALTTRACKER_FOLLOWUP_REPLY_TO", "").strip()
    address = os.environ.get("SALTTRACKER_FOLLOWUP_ADDRESS", "").strip()
    name = os.environ.get("SALTTRACKER_FOLLOWUP_NAME", "").strip() or "Lewis Eastwood"
    missing = []
    if not reply or reply.upper().startswith("SET "):
        missing.append("SALTTRACKER_FOLLOWUP_REPLY_TO")
    if not address or address.upper().startswith("SET "):
        missing.append("SALTTRACKER_FOLLOWUP_ADDRESS")
    if missing:
        raise SenderConfigError(
            "Refusing to generate drafts: set "
            + " and ".join(missing)
            + ". The RTKL requires a name and a verifiable postal address, not a reply-to alone."
        )
    return reply, address, name


def aoro_email(row: dict) -> str | None:
    status = (row.get("aoro_status") or "").strip().upper()
    email = (row.get("aoro_email") or "").strip()
    if status != "VERIFIED":
        return None
    if not email or email.upper() == "UNVERIFIED" or "@" not in email:
        return None
    return email


def _stamp_headers(msg: EmailMessage, stamp: str) -> None:
    msg["Date"] = formatdate(localtime=True)
    msg["X-SaltTracker-Draft-Stamp"] = stamp


def draft_message(row: dict, stamp: str) -> EmailMessage:
    reply, address, name = sender_fields()
    msg = EmailMessage()
    msg["Subject"] = SUBJECT
    msg["From"] = os.environ.get("SALTTRACKER_SMTP_FROM", reply)
    dest = aoro_email(row)
    if dest:
        msg["To"] = dest
    else:
        msg["To"] = "UNVERIFIED-DO-NOT-SEND"
    _stamp_headers(msg, stamp)
    msg["X-SaltTracker-County"] = row["county"]
    msg["X-SaltTracker-AORO-Status"] = row.get("aoro_status") or ""
    greeting = greeting_line(row)
    if not dest:
        mail_to = (row.get("aoro_address") or "").strip()
        officer = (row.get("aoro_officer") or "Agency Open Records Officer").strip()
        greeting = (
            f"FILE BY MAIL — do not email. Mail to {officer}, {mail_to}.\n\n"
            + greeting
        )
    msg.set_content(COUNTY_BODY.format(
        greeting=greeting,
        county=row.get("county") or "the",
        sender_name=name,
        sender_address=address,
        reply_to=reply,
    ))
    return msg


def draft_dgs_message(stamp: str) -> EmailMessage:
    reply, address, name = sender_fields()
    msg = EmailMessage()
    msg["Subject"] = DGS_SUBJECT
    msg["From"] = os.environ.get("SALTTRACKER_SMTP_FROM", reply)
    msg["To"] = DGS_AORO["email"]
    _stamp_headers(msg, stamp)
    msg["X-SaltTracker-Agency"] = "DGS"
    msg["X-SaltTracker-AORO"] = DGS_AORO["officer"]
    body = dgs_letter_text(reply, address, name)
    pdf_bytes, form_status = build_dgs_form_attachment(reply, address, name)
    if form_status != "filled":
        body = body.rstrip() + "\n\n" + dgs_form_paste_block(reply, address, name)
        msg["X-SaltTracker-DGS-Form"] = form_status
    else:
        msg["X-SaltTracker-DGS-Form"] = "filled"
    msg.set_content(body)
    if pdf_bytes:
        msg.add_attachment(
            pdf_bytes,
            maintype="application",
            subtype="pdf",
            filename=DGS_FORM_FILENAME,
        )
    return msg


def write_workbook(rows: list[dict], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "PA procurement contacts"
    header = list(rows[0].keys()) if rows else []
    fill = PatternFill("solid", fgColor="1B3A4B")
    font = Font(color="FFFFFF", bold=True)
    ws.append(header)
    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
    for row in rows:
        ws.append([row.get(c, "") for c in header])
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width = min(48, max(14, len(str(col[0].value or "")) + 4))
    note = wb.create_sheet("How to use")
    note["A1"] = (
        "One letter per AORO. County letters ask that county's own COSTARS and "
        "off-contract salt for FY2022–FY2027 — not municipal purchases. The DGS "
        "letter asks for weekly shipment reports FY2022–FY2027, FY2022–FY2023 "
        "award/price files, FY2025 estimates, and a full 67-county FY2023 "
        "estimates table, and goes to DGS-RTK@pa.gov, not the commodity specialist. "
        "Drafts require SALTTRACKER_FOLLOWUP_REPLY_TO and "
        "SALTTRACKER_FOLLOWUP_ADDRESS. Washington AORO email is UNVERIFIED "
        "(mail the Chief Clerk — the DocuSign link is unconfirmed). "
        "The DGS row is for the response clock only: five business days from "
        "AORO receipt (received_on), with a 30-day extension likely. Fill "
        "received_on when the officer has the request; responded_on when a "
        "written response arrives. Run --clock to compute due dates. Do not "
        "put a home address in this workbook. "
        "Sending requires --send and SALTTRACKER_FOLLOWUP_CONFIRM=YES."
    )
    note["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    note.row_dimensions[1].height = 80
    note.column_dimensions["A"].width = 88
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def write_drafts(rows: list[dict], stamp: str) -> list[Path]:
    sender_fields()
    DRAFTS.mkdir(parents=True, exist_ok=True)
    written = []
    for row in rows:
        if is_dgs_row(row):
            continue
        msg = draft_message(row, stamp)
        slug = row["county"].lower().replace(" ", "_")
        path = DRAFTS / f"{stamp[:10]}_{slug}.eml"
        path.write_bytes(bytes(msg))
        written.append(path)
    dgs_path = DRAFTS / f"{stamp[:10]}_dgs.eml"
    dgs_path.write_bytes(bytes(draft_dgs_message(stamp)))
    written.append(dgs_path)
    return written


def send_drafts(rows: list[dict], stamp: str) -> list[str]:
    if os.environ.get("SALTTRACKER_FOLLOWUP_CONFIRM", "").strip() != "YES":
        raise RuntimeError("Refusing to send: set SALTTRACKER_FOLLOWUP_CONFIRM=YES")
    host = os.environ.get("SALTTRACKER_SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SALTTRACKER_SMTP_HOST is required to send")
    port = int(os.environ.get("SALTTRACKER_SMTP_PORT", "587"))
    user = os.environ.get("SALTTRACKER_SMTP_USER", "")
    password = os.environ.get("SALTTRACKER_SMTP_PASS", "")
    notes = []
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        for row in rows:
            if is_dgs_row(row):
                continue
            if not aoro_email(row):
                notes.append(f"skipped {row['county']}: AORO email UNVERIFIED")
                continue
            msg = draft_message(row, stamp)
            smtp.send_message(msg)
            notes.append(f"sent {row['county']} -> {aoro_email(row)}")
        dgs_msg = draft_dgs_message(stamp)
        smtp.send_message(dgs_msg)
        notes.append(f"sent DGS -> {DGS_AORO['email']}")
    return notes


def _notified_path() -> Path:
    return OUT / "notified.json"


def _load_notified() -> dict:
    path = _notified_path()
    if not path.is_file():
        return {"sent": [], "responded": []}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"sent": [], "responded": []}
    data.setdefault("sent", [])
    data.setdefault("responded", [])
    return data


def _save_notified(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    _notified_path().write_text(json.dumps(state, indent=2))


def _followup_ping(kind: str, text: str) -> list[str]:
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from salttracker.notify import followup_event
    return followup_event(kind, text)


def notify_letters_sent(notes: list[str]) -> list[str]:
    """After a confirmed send: ping once per newly sent letter. No-op with no channel."""
    sent = [n for n in notes if n.startswith("sent ")]
    if not sent:
        return []
    state = _load_notified()
    new = [n for n in sent if n not in state["sent"]]
    if not new:
        return []
    ping = _followup_ping("sent", "RTK letters sent:\n" + "\n".join(new))
    if ping and all("failed" not in n for n in ping):
        state["sent"].extend(new)
        _save_notified(state)
    return ping


def notify_new_responses(rows: list[dict], today: dt.date) -> list[str]:
    """When --clock sees a new responded_on, ping once. Does not send government mail."""
    state = _load_notified()
    new = []
    for row in rows:
        info = clock_row(row, today)
        if info["status"] != "responded":
            continue
        key = f"{info['label']}:{(row.get('responded_on') or '').strip()}"
        if key in state["responded"]:
            continue
        new.append(key)
    if not new:
        return []
    ping = _followup_ping(
        "responded",
        "Written RTK response recorded:\n" + "\n".join(new),
    )
    if ping and all("failed" not in n for n in ping):
        state["responded"].extend(new)
        _save_notified(state)
    return ping


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock", action="store_true",
                        help="Print the RTKL response/appeal clock from contacts.csv; does not send or write drafts")
    parser.add_argument("--send", action="store_true",
                        help="Send drafts via SMTP (also requires SALTTRACKER_FOLLOWUP_CONFIRM=YES)")
    args = parser.parse_args(argv)

    load_local_env()
    rows = load_contacts()
    if not rows:
        print("No contacts in", CONTACTS, file=sys.stderr)
        return 1

    if args.clock:
        sys.stdout.write(format_clock_report(rows, dt.date.today()))
        for line in notify_new_responses(rows, dt.date.today()):
            print(line)
        return 0

    stamp = dt.datetime.now().replace(microsecond=0).isoformat()
    OUT.mkdir(parents=True, exist_ok=True)
    xlsx = OUT / "PA_procurement_contacts.xlsx"
    write_workbook(rows, xlsx)
    print(f"Wrote {xlsx}")
    try:
        drafts = write_drafts(rows, stamp)
    except SenderConfigError as exc:
        print(str(exc), file=sys.stderr)
        print("No mail sent (drafts not generated).")
        return 1
    print(f"Wrote {len(drafts)} drafts under {DRAFTS}")
    for p in drafts:
        print(" ", p.name)

    if args.send:
        notes = send_drafts(rows, stamp)
        for line in notes:
            print(line)
        for line in notify_letters_sent(notes):
            print(line)
    else:
        print("No mail sent (draft only). Pass --send only after reviewing the .eml files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
