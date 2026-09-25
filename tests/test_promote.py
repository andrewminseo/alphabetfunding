"""promote.py: moves approved non-FAIL rows, 17 columns including floating_margin_bps."""

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import promote  # noqa: E402
from extract_terms import BASE_COLS, PENDING_COLS  # noqa: E402

OLD_16 = [c for c in BASE_COLS if c not in ("floating_margin_bps", "coupon_frequency")]


def row(tid, mat, cpn, status, approved, rate_type="fixed", margin=""):
    r = {c: "" for c in PENDING_COLS}
    r.update(tranche_id=tid, issuer="Alphabet Inc.", currency="USD", principal_local="500000000",
             coupon_pct=cpn, issue_date="2025-11-06", maturity_date=mat, rate_type=rate_type,
             status=status, approved=approved, floating_margin_bps=margin)
    return r


def run(tmp_path, monkeypatch, pending_rows, db_cols=OLD_16):
    db = tmp_path / "tranches.csv"
    pend = tmp_path / "pending.csv"
    existing = {c: "" for c in db_cols} | {"tranche_id": "GOOGL-2025-05-2030", "currency": "USD",
                                           "maturity_date": "2030-05-15", "coupon_pct": "4.000"}
    pd.DataFrame([existing], columns=db_cols).to_csv(db, index=False)
    pd.DataFrame(pending_rows, columns=PENDING_COLS).to_csv(pend, index=False)
    monkeypatch.setattr(promote, "TRANCHES", db)
    monkeypatch.setattr(promote, "PENDING", pend)
    monkeypatch.setattr(sys, "argv", ["promote.py"])
    promote.main()
    return pd.read_csv(db, dtype=str).fillna(""), pd.read_csv(pend, dtype=str).fillna("")


def test_base_cols_are_16_plus_margin_and_frequency():
    assert len(BASE_COLS) == 18 and BASE_COLS[-2:] == ["floating_margin_bps", "coupon_frequency"]
    assert "floating_margin_bps" in PENDING_COLS


def test_frn_margin_carried_into_tranches(tmp_path, monkeypatch):
    db, pend = run(tmp_path, monkeypatch, [
        row("GOOGL-2025-11-2028-FRN", "2028-11-15", "", "PASS", "yes", "floating", "52"),
    ])
    assert list(db.columns) == BASE_COLS                      # 16-column file upgraded to 17
    frn = db.set_index("tranche_id").loc["GOOGL-2025-11-2028-FRN"]
    assert frn["floating_margin_bps"] == "52"
    assert db.set_index("tranche_id").loc["GOOGL-2025-05-2030", "floating_margin_bps"] == ""
    assert pend.empty


def test_fail_unapproved_and_duplicates_stay_pending(tmp_path, monkeypatch):
    db, pend = run(tmp_path, monkeypatch, [
        row("NEW-OK", "2031-01-01", "3.000", "PASS", "yes"),
        row("NEW-FAIL", "2032-01-01", "3.000", "FAIL", "yes"),
        row("NEW-UNAPPROVED", "2033-01-01", "3.000", "WARN", ""),
        row("GOOGL-2025-05-2030", "2030-05-15", "4.000", "PASS", "yes"),
    ], db_cols=BASE_COLS)
    assert list(db["tranche_id"]) == ["GOOGL-2025-05-2030", "NEW-OK"]
    assert list(pend["tranche_id"]) == ["NEW-FAIL", "NEW-UNAPPROVED", "GOOGL-2025-05-2030"]
