"""Hard assertions block the routine label."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from salttracker import assertions  # noqa: E402


def test_empty_provenance_is_a_failure():
    fired = assertions.empty_provenance([
        {"source_doc": "mystery.pdf", "source_url": None, "source_page_url": None},
        {"source_doc": "ok.pdf", "source_url": "https://example.com/a.pdf"},
    ])
    assert len(fired) == 1
    assert fired[0]["kind"] == "empty_provenance"


def test_supplier_dropped_is_the_cargill_case():
    fired = assertions.supplier_dropped(
        {"MI": {"Detroit Salt", "Compass Minerals", "Cargill"}},
        {"MI": {"Detroit Salt", "Compass Minerals"}},
    )
    assert any(a["vendor"] == "Cargill" for a in fired)


def test_sha256_alias_is_the_join_bug():
    fired = assertions.sha256_alias(
        [{"name": "PA_estimates_FY2027.pdf", "sha256": "abc"}],
        [{"name": "PA_estimates_FYx.pdf", "sha256": "abc"}],
    )
    assert fired[0]["kind"] == "sha256_alias"
    assert fired[0]["alias_of"] == "PA_estimates_FYx.pdf"


def test_renumber_is_reported_not_auto_linked():
    fired = assertions.contract_renumbering(
        {"260000000712"},
        {"180000000768", "180000000787"},
        [{"filename": "MI_FY2027_DetroitSalt_260000000712.pdf",
          "url": "https://www.michigan.gov/712.pdf",
          "effective_date": "2026-07-01", "expiration_date": "2027-08-31"}],
    )
    assert fired
    assert "Not auto-linked" in fired[0]["detail"]
    assert "260000000712" in fired[0]["detail"]
    assert "180000000768" in fired[0]["detail"]


def test_known_live_numbers_do_not_re_fire_every_week():
    known = {"180000000768", "180000000787", "260000000712", "260000000713"}
    fired = assertions.contract_renumbering({"260000000712"}, known, [])
    assert fired == []


def test_dates_outside_fy():
    fired = assertions.dates_outside_fiscal_year([
        {"filename": "x.pdf", "fiscal_year": 2026,
         "effective_date": "2025-07-29", "expiration_date": "2026-08-31"},
    ])
    kinds = {a["field"] for a in fired}
    assert "effective_date" in kinds
    assert "expiration_date" not in kinds


def test_live_move_over_10_percent_is_not_routine():
    live = {("MI", 2027): 1000.0}
    proposed = {("MI", 2027): 1110.0}
    fired = assertions.yoy_move(live, proposed)
    assert any(a["kind"] == "live_move" for a in fired)


def test_live_move_under_10_percent_is_ok():
    live = {("MI", 2027): 1000.0}
    proposed = {("MI", 2027): 1090.0}
    assert assertions.yoy_move(live, proposed) == []


def test_historical_yoy_already_on_chart_is_not_re_fired():
    live = {("MI", 2026): 1000.0, ("MI", 2027): 1160.0}
    proposed = {("MI", 2026): 1000.0, ("MI", 2027): 1160.0}
    assert assertions.yoy_move(live, proposed) == []


def test_new_fy_over_10_percent_is_yoy_move():
    live = {("MI", 2026): 1000.0}
    proposed = {("MI", 2026): 1000.0, ("MI", 2027): 1111.0}
    fired = assertions.yoy_move(live, proposed)
    assert any(a["kind"] == "yoy_move" for a in fired)


def test_column_drift_on_familiar_file_without_matching_set():
    fired = assertions.column_drift([
        {"filename": "est.pdf", "familiar": True, "columns_match": False,
         "column_drift": "no extracted header set is an exact match"},
    ])
    assert fired[0]["kind"] == "column_drift"


def test_undetermined_blocks_routine():
    fired = assertions.undetermined_fields([
        {"filename": "x.pdf", "undetermined": ["column signature"]},
    ])
    assert fired[0]["kind"] == "undetermined"


def test_tons_basis_new_value_is_not_routine():
    fired = assertions.tons_basis_mismatch(
        [{"filename": "x.pdf", "state": "MI", "tons_basis": "purchased", "familiar": True}],
        {"MI": {"derived"}},
    )
    assert fired[0]["kind"] == "tons_basis_mismatch"


def test_pa_mixed_basis_already_in_series_is_ok():
    fired = assertions.tons_basis_mismatch(
        [{"filename": "est.pdf", "state": "PA", "tons_basis": "committed_estimate", "familiar": True}],
        {"PA": {"printed", "committed_estimate"}},
    )
    assert fired == []
