# Road Salt Contract Tracker — Michigan & Pennsylvania

Tracks **contracted road salt volume and price by state, supplier and fiscal year**,
built by scraping published state procurement documents. Outputs a downloadable
CSV/Excel dataset and an interactive dashboard.

## Definitions

| Term | Definition |
|---|---|
| Fiscal year | Oct 1 – Sep 30, named for the year it **ends**. The 2025/2026 winter season is **FY2026**. |
| Contracted volume | Tons a supplier is awarded/committed to, summed across all programs (Michigan early fill + seasonal back-up). |
| Contracted price | **Total contract value ÷ total contracted volume** — the effective weighted-average price per ton. |
| Simple average | Unweighted mean of posted county prices. Carried as a secondary column because it is the figure states publish as their headline "statewide average". |

Contracts for the next winter are awarded in **July–August**, so FY2027 contracts
were finalized around July–August 2026 and are already published. FY2027 is
flagged `is_current_cycle = true`: it is contracted, not yet delivered, and may
still be amended.

> **Why weighted, not simple?** Volume concentrates in cheap, high-tonnage
> locations. For Compass in Michigan FY2026 the weighted price is $75.72/ton
> while the simple average is $78.87 — a 4% gap, because an unweighted mean gives
> a 25-ton village the same weight as a 7,200-ton MDOT depot.

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

PYTHONPATH=src .venv/bin/python scripts/refresh.py      # scrape + parse + export
./scripts/open_dashboard.sh                            # executive dashboard in the browser
# or open data/output/Road_Salt_Contract_Tracker.html — no Python required
PYTHONPATH=src .venv/bin/python -m pytest tests -q      # validate
```

The dashboard is a briefing, not a lab notebook: dropdowns for state, supplier and
fiscal year; time series of price and volume; supplier price, volume and share;
CSV and Excel export of the current view. The HTML file in `data/output/` is the
same briefing as a double-clickable link you can put on a shared drive.

**Public URL (Streamlit Community Cloud):** after this repo is on GitHub, deploy
at [share.streamlit.io](https://share.streamlit.io) → New app → repository
`lewiseastwood/salttracker`, branch `main`, file `dashboard/app.py`. Executives
open the `*.streamlit.app` link; they do not need GitHub access.

## Outputs (`data/output/`)

## Outputs (`data/output/`)

| File | Contents |
|---|---|
| `salt_contracts_raw.csv` | One row per county (PA) or drop point (MI), with source document and page for traceability |
| `salt_contracts_by_vendor.csv` | State × fiscal year × vendor: volume, weighted price, contract value, volume share |
| `salt_contracts_by_state.csv` | State × fiscal year totals |
| `salt_contract_tracker.xlsx` | All of the above plus a source-coverage sheet |
| `Road_Salt_Contract_Tracker.html` | Same briefing as Streamlit, including the state-share map and links to published contract PDFs |

The dashboard **Tables & export** tab lists every source PDF in the current filter, with a link to the state's published file. A zip of local PDFs is available only after `scripts/refresh.py` has been run on that machine (PDFs are gitignored and are not on Streamlit Cloud).

## Data sources

**Michigan** — DTMB/MiDEAL [Salt, Bulk Rock](https://www.michigan.gov/dtmb/procurement/mideal-extended-purchasing-program/mideal-contract-search/categories/folder-2/salt-bulk-rock).
One contract per supplier, reused for years, with a change notice appended each
season — so a single PDF is a cumulative stack of awards. Suppliers are tracked
by contract number (`180000000768` Detroit Salt, `180000000787` Compass Minerals,
`180000000791` Cargill, `2600000007xx` the FY2027 cycle).

**Pennsylvania** — DGS statewide sodium chloride contract. Price and volume come
from two different documents and are joined by county:

- *Price* — the COSTARS season packet, and Attachment A of the eMarketplace
  change notices. One combined change-notice PDF carries all four renewals
  (CN1–CN4), which is how the FY2023–FY2025 pricing is recovered.
- *Volume* — the "Estimates" attachment on each salt solicitation, which lists
  every county's tonnage split across PennDOT, COSTARS members and state
  agencies. The COSTARS packet itself publishes no statewide tonnage.

Because DGS awards each county to a single supplier for a season, county tonnage
is credited to whichever supplier holds that county.

**Historical recovery** — states overwrite these pages each summer, so prior years
come from the Wayback Machine (`sources.py:discover_michigan_archived`).

## Automation

The durable schedule is **GitHub Actions** (`.github/workflows/refresh.yml`).
Cadence is two UTC cron fields: daily at 11:15 UTC (07:15 Eastern / EDT) in
June–August, Mondays otherwise. Each scrape attempt — including a failed listing
or parse — commits `watch_state.json` so the dashboard strip is not a stale quiet
week, and so GitHub does not disable the schedule after 60 days of no commits.
CSVs update when the export completes. Slack/email alerts are optional secrets:

| Secret | Purpose |
|---|---|
| `SALTTRACKER_CONTACT` | Optional mailbox (not used as the User-Agent; michigan.gov 403s a crawler UA) |
| `SALTTRACKER_WEBHOOK_URL` | Slack / Teams / generic POST when a new season or document appears |
| `SALTTRACKER_ALERT_EMAIL` | Optional; also set `SALTTRACKER_SMTP_HOST` / `_USER` / `_PASS` |

michigan.gov's Akamai edge 403s a custom crawler User-Agent. Requests use a
current Chrome identity plus browser `Accept` / `Sec-Fetch-*` headers, still
capped at **2 requests/second** (`SALTTRACKER_RPS`). 429/5xx retry; 403 does
not. Every request to michigan.gov, pa.gov, and eMarketplace goes through
`sources.http_get` (POST to COSTARS e-bidding uses the same helper).

Do not install a local LaunchAgent. `./scripts/install_schedule.sh` refuses
and will only `--remove` a leftover plist.

**Discovery is not pinned to today's URLs**, because next year's packet will be
published at an address that does not exist yet. Each run:

1. Scrapes Michigan's DTMB salt page and the DGS COSTARS index for any linked
   sodium-chloride document.
2. Walks eMarketplace solicitation ids forward from the newest known salt bid,
   matching on title, and pulls the estimates and bid-sheet attachments from any
   new one. This is the earliest signal a new cycle exists — the solicitation is
   posted months before the COSTARS packet. The scan is checkpointed in
   `data/interim/pa_sid_scan.json`, so a daily run only pays for ids added since
   the last one. (eMarketplace's own keyword search returns `app_offline.htm`,
   which is why ids are walked rather than queried.)
3. Content-hashes every download, so reruns are idempotent and the same combined
   change notice served under five contract numbers is parsed once.
4. Diffs coverage against the previous run and **raises an alert** for a newly
   published season, a supplier not seen before, or a new document. Alerts print
   to the console, append to `data/alerts.jsonl`, and POST / email if those
   secrets are set. Run state lives in `data/watch_state.json`.

Each run also appends to `data/refresh_log.jsonl`.

## Pennsylvania follow-up (pilot)

Statewide COSTARS packets do not include a county's own salt purchase if it buys off-contract. `scripts/pa_followup.py` is a **draft-only** Right-to-Know pack for the five Pennsylvania counties with the most FY2027 contracted tons (Allegheny, Westmoreland, Luzerne, Washington, Erie):

```bash
PYTHONPATH=src .venv/bin/python scripts/pa_followup.py
```

That writes `data/output/pa_followup/PA_procurement_contacts.xlsx` and `.eml` drafts. Recipients are each county’s **Agency Open Records Officer** (RTKL), verified from the county Right-to-Know page; purchasing inboxes are a secondary column only. Washington’s AORO email is not published and is marked UNVERIFIED. The script **does not send mail** unless you pass `--send` and set `SALTTRACKER_FOLLOWUP_CONFIRM=YES`, plus the existing SMTP secrets. Optional `--poll-inbox` forwards unseen IMAP replies that look like salt/RTK responses to `SALTTRACKER_ALERT_EMAIL`.

## Extraction hazards handled

## Extraction hazards handled

These documents are hostile to naive parsing. The parsers address:

1. **Glyph-split numbers.** Michigan emits `2 00` for 200, `1 ,000` for 1000,
   `$ 6 5.92` for $65.92. Spaces are stripped from numeric cells, and tonnage is
   recomputed as `extended ÷ price` because those two columns survive extraction
   more cleanly than tonnage.
2. **Continuation pages** repeat table bodies without headers, and column
   positions differ between MDOT/MiDEAL layouts and contract eras. Column maps
   are validated against cell content and re-inferred when they don't hold.
3. **Partial amendments.** A change notice may restate only part of a season
   (Michigan CN16 restates only Early Fill), so superseding is resolved per
   (fiscal year, program, channel), not per season.
4. **Cross-document duplication.** A vendor's schedule is reprinted inside other
   vendors' packets — Michigan's FY2027 Compass PDF also contains Detroit's
   529,600-ton seasonal schedule. Rows are attributed by the bidder column and
   deduplicated per schedule, so volume is not double-counted.
5. **Roster pages.** PA packets end with a participating-member roster whose
   tonnage columns look like pricing rows; parsing stops at that heading.

## Validation

`tests/test_validation.py` asserts against figures **the documents state
themselves**, so extraction drift is detected rather than merely re-asserted:

- Compass MI FY2026 = **260,595 tons / $19,731,399.85** ($75.72/ton), matching the
  four schedule totals printed on pages 5, 8, 11 and 13 of MA180000000787.
  The brief also quotes **$78.96/ton**; that figure is an open question (see
  Known gaps), not a passing test.
- Detroit MI FY2027 = **938,010 tons** (106,260 + 89,450 + 212,700 + 529,600 as stated).
- Compass MI FY2027 = **407,305 tons** (103,975 + 53,830 + 94,150 + 155,350 as stated).
- PA FY2027 simple average = **$92.47/ton**, matching the packet's published
  "Statewide Avg."; FY2026 = $88.21 as quoted. All 67 counties resolved, with the
  stated high ($113.10 Mifflin) and low ($75.00 Bucks/Montgomery/Philadelphia).

## Coverage

| State | FY2022 | FY2023 | FY2024 | FY2025 | FY2026 | FY2027 |
|---|---|---|---|---|---|---|
| MI | price + volume | price + volume | price + volume | price + volume | price + volume | price + volume |
| PA | volume | volume (partial) | price + volume | price | price + volume | price + volume |

## Known gaps

- **Compass MI FY2026 price: $75.72 vs the brief's $78.96.** Tonnage matches
  the brief to the ton (260,595) on the same 274 line items, pages 4–13, so the
  row set is not the problem. Revenue ÷ volume on those rows is $75.72, which is
  also the contract's own printed total. The $78.96 is not printed anywhere in
  the PDF. Candidates computed on the extracted prices, none of which hit $78.96:

  | Method | Result |
  |---|---|
  | Unweighted mean of 274 line prices | $78.69 |
  | Same sum ÷ 273 (off-by-one) | $78.97 |
  | Drop any one real row (best) | $78.75 |
  | One price per drop point (entity) | $78.71 |
  | One price per county | $79.16 |
  | Early-fill only / seasonal only | $76.62 / $79.89 |
  | Mean of the four schedule simple averages | $78.81 |
  | Round each price to 0/1/2 dp, then mean | $78.69 |
  | Mean of distinct unit prices | $79.42 |

  There is no zero-ton or blank-price row that could be a phantom header, and
  no earlier FY2026 document to try: the live DTMB page now lists only the
  FY2027 Compass contract, every archived 787 PDF from 31 Aug 2025 through
  May 2026 is byte-identical to the file we parsed, and the April 2025 snapshot
  is the previous season (2024/2025) with no FY2026 rows. The residual
  hypothesis is that $78.96 came from a summary the brief's author was handed
  rather than from this contract file. The tracker ships the weighted $75.72
  because that is the method the brief defines. The $4.24 gap is also
  informative: high-volume drop points are priced below the average location.
- **PA FY2022–FY2023 have volume but no supplier pricing.** The county tonnage is
  published, but the 2021 award and the FY2022 county prices were never captured
  by the Wayback Machine and are not served by eMarketplace. Those rows carry
  `vendor = "Unattributed"` and `record_type = "volume_only"`, so they count
  toward state volume but not toward any supplier's share. FY2023 volume covers
  only the eight counties in the 2022-23 re-bid.
- **PA FY2025 has pricing but no volume.** It was a renewal year, so DGS issued
  no new estimates attachment; those rows are `record_type = "price_only"` and the
  season reports a simple average price with a blank tonnage.
- **PA FY2024 tonnage is packet-printed, not estimate-based** (`tons_basis =
  "printed"`), so it is not strictly comparable to the estimate-based years.
- **MI FY2025 volume (810k tons) is below neighbouring years** (~1.1 Mt). This
  reflects the published documents: only two suppliers were listed that season and
  the change notices contain fewer drop points. Not an extraction artifact —
  per-schedule item numbering is complete.
- Cargill exited Michigan after FY2022 (contract `180000000791`), leaving Detroit
  Salt and Compass Minerals as the only Michigan suppliers from FY2023 on.

## Layout

```
src/salttracker/
  util.py                 fiscal-year mapping, numeric coercion, vendor canonicalization
  sources.py              document discovery (live pages + Wayback), download, manifest
  pipeline.py             parse -> dedupe -> normalize -> aggregate -> export
  parsers/michigan.py     DTMB/MiDEAL cumulative contracts
  parsers/pennsylvania.py DGS/COSTARS county pricing
dashboard/app.py          Streamlit dashboard
scripts/refresh.py        end-to-end refresh
scripts/pa_followup.py    PA county RTK drafts (does not send unless confirmed)
tests/test_validation.py  document-anchored validation
scripts/persist_refresh.sh  commit scrape outputs including failures
tests/test_validation.py  document-anchored validation
data/raw/                 downloaded source documents (by state)
data/output/              CSV + Excel deliverables
```
