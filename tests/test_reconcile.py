"""Reconciliation math in scripts/reconcile.py."""

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import reconcile  # noqa: E402

FX = pd.Series({"USD": 1.0, "EUR": 1.2, "JPY": 0.007})
AS_OF = pd.Timestamp("2026-09-24")
PERIOD_END = pd.Timestamp("2025-12-31")

TRANCHES = pd.DataFrame([
    # id, currency, principal, issue, maturity
    ("A", "USD", 1_000_000_000, "2020-08-05", "2025-08-15"),   # matured before period end
    ("B", "USD", 2_000_000_000, "2025-05-01", "2030-05-15"),   # outstanding at period end
    ("C", "eur ", 1_000_000_000, "2025-05-06", "2033-05-06"),  # 1.2B USD; messy code
    ("D", "JPY", 100_000_000_000, "2026-05-16", "2036-05-16"),  # 0.7B USD, issued after period end
    ("E", "USD", 500_000_000, "2024-01-01", "2026-03-15"),     # outstanding at period end, matured since
    ("F", "USD", 300_000_000, "2025-01-01", "2025-12-31"),     # matures on period end: excluded
    ("G", "USD", 400_000_000, "2026-02-01", "2026-06-01"),     # issued after, matured by as-of
], columns=["tranche_id", "currency", "principal_local", "issue_date", "maturity_date"])

FACTS = pd.DataFrame([
    ("LongTermDebt", "USD", 3_000_000_000, "2024-12-31", "2025-02-05", "acc-old"),
    ("LongTermDebt", "USD", 4_000_000_000, "2025-12-31", "2026-02-05", "acc-new"),
    ("LongTermDebtNoncurrent", "USD", 9_999_000_000, "2026-06-30", "2026-07-23", "acc-q2"),
], columns=["tag", "unit", "value", "period_end", "filed", "accession"])


def test_outstanding_at_period_end():
    t = reconcile.to_usd(TRANCHES, FX)
    live = reconcile.outstanding_at(t, PERIOD_END)
    assert list(live["tranche_id"]) == ["B", "C", "E"]
    assert live["principal_usd"].sum() == pytest.approx(2e9 + 1.2e9 + 0.5e9)


def test_issued_after_period_end_still_outstanding():
    t = reconcile.to_usd(TRANCHES, FX)
    after = reconcile.issued_after(t, PERIOD_END, AS_OF)
    assert list(after["tranche_id"]) == ["D"]


def test_latest_long_term_debt_uses_latest_period_and_tag():
    r = reconcile.latest_long_term_debt(FACTS)
    assert r["value"] == 4_000_000_000 and r["accession"] == "acc-new"


def test_gap_in_dollars_and_percent():
    r = reconcile.reconcile(TRANCHES, FX, FACTS, AS_OF)
    assert r["n_tranches"] == 3
    assert r["tranche_face_usd"] == pytest.approx(3.7e9)
    assert r["gap_usd"] == pytest.approx(3.7e9 - 4.0e9)
    assert r["gap_pct"] == pytest.approx(-7.5)
    assert r["issued_after_period_end_usd"] == pytest.approx(0.7e9)
    assert r["n_issued_after"] == 1


def test_missing_fx_raises():
    with pytest.raises(ValueError, match="GBP"):
        reconcile.to_usd(
            pd.DataFrame([("X", "GBP", 1, "2026-01-01", "2030-01-01")], columns=TRANCHES.columns), FX)


def test_fx_on_uses_the_requested_date(tmp_path):
    h = tmp_path / "fx_history.csv"
    pd.DataFrame([
        ("2025-12-31", "EUR", 1.175005), ("2025-12-31", "USD", 1.0),
        ("2026-09-23", "EUR", 1.14579), ("2026-09-23", "USD", 1.0),
    ], columns=["date", "currency", "usd_per_unit"]).to_csv(h, index=False)
    assert reconcile.fx_on("2025-12-31", h)["EUR"] == pytest.approx(1.175005)
    with pytest.raises(ValueError, match="update_fx.py --date 2025-06-30"):
        reconcile.fx_on("2025-06-30", h)


def test_reported_total_falls_back_to_carrying_amount_tag():
    facts = pd.concat([FACTS, pd.DataFrame([
        ("DebtInstrumentCarryingAmount", "USD", 101_085_000_000, "2026-06-30", "2026-07-23", "acc-q2"),
        ("DebtInstrumentCarryingAmount", "USD", 4_000_000_000, "2025-12-31", "2026-02-05", "acc-new"),
    ], columns=FACTS.columns)])
    q2 = reconcile.latest_long_term_debt(facts)
    assert (q2["tag"], q2["value"]) == ("DebtInstrumentCarryingAmount", 101_085_000_000)
    ye = reconcile.latest_long_term_debt(facts, "2025-12-31")
    assert ye["tag"] == "LongTermDebt"                     # preferred when both exist


def test_other_debt_is_separate_known_item():
    r = reconcile.reconcile(TRANCHES, FX, FACTS, AS_OF, other_debt_usd=300_000_000)
    assert r["reported_notes_usd"] == pytest.approx(3.7e9)
    assert r["gap_usd"] == pytest.approx(0.0)
    assert r["reported_ltd_usd"] == 4_000_000_000


def test_other_long_term_debt_read_from_filing_table(tmp_path, monkeypatch):
    html = ("<table><tr><td>2026 Japanese yen notes</td><td>2029 - 2066</td><td>0</td><td>3,566</td></tr>"
            "<tr><td>Other long-term debt</td><td></td><td>0</td><td>1,686</td></tr></table>")
    monkeypatch.setattr(reconcile, "RAW", tmp_path)
    (tmp_path / "acc-q2_q.htm").write_text(html)
    idx = tmp_path / "idx.csv"
    pd.DataFrame([("acc-q2", "q.htm", "https://x/q.htm")],
                 columns=["accessionNumber", "primaryDocument", "url"]).to_csv(idx, index=False)
    value, note = reconcile.other_long_term_debt("acc-q2", idx)
    assert value == 1_686_000_000 and "acc-q2" in note
