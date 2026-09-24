"""treasury.py analytics on the four verified April 2025 USD tranches."""

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import treasury  # noqa: E402

AS_OF = pd.Timestamp("2026-09-18")
SETTLEMENT = "2025-05-01"  # settlement date of the 2025-04-28 FWP deal


@pytest.fixture
def debt(tmp_path):
    g = pd.read_csv(ROOT / "tests" / "golden" / "2025-04-28_usd.csv")
    t = pd.DataFrame({
        "tranche_id": "GOOGL-2025-05-" + g["maturity"].str[:4],
        "currency": "USD",
        "principal_local": g["principal"],
        "coupon_pct": g["coupon"],
        "issue_date": SETTLEMENT,
        "maturity_date": g["maturity"],
        "rate_type": "fixed",
    })
    path = tmp_path / "tranches.csv"
    t.to_csv(path, index=False)
    return treasury.load(path, AS_OF)


def test_summary(debt):
    s = treasury.summary(debt)
    assert s["n_tranches"] == 4
    assert s["outstanding_usd"] == 5_000_000_000
    wa = (750 * 4.0 + 1250 * 4.5 + 1500 * 5.25 + 1500 * 5.3) / 5000
    assert s["wa_coupon_pct"] == pytest.approx(wa)
    assert s["annual_coupon_usd"] == pytest.approx(5e9 * wa / 100)


def test_maturity_ladder(debt):
    ladder = treasury.maturity_ladder(debt)
    assert ladder.index.min() == 2030 and ladder.index.max() == 2065
    assert ladder.loc[2030, "TOTAL"] == 750_000_000
    assert ladder.loc[2035, "TOTAL"] == 1_250_000_000
    assert ladder.loc[2031, "TOTAL"] == 0
    assert ladder["TOTAL"].sum() == 5_000_000_000


def test_refi_sensitivity(debt):
    refi = treasury.refi_sensitivity(debt, AS_OF, 5, [0, 100])
    # Only the 2030 note matures within 5 years of 2026-09-18.
    assert (refi["principal_refinanced_usd"] == 750_000_000).all()
    d0, d100 = refi["delta_annual_interest_usd"]
    assert d100 - d0 == pytest.approx(750_000_000 * 0.01)


# --- Robustness -----------------------------------------------------------

FX = pd.Series({"USD": 1.0, "EUR": 1.2})
COLS = ["tranche_id", "currency", "principal_local", "coupon_pct", "issue_date",
        "maturity_date", "rate_type", "benchmark", "floating_margin_bps"]
FIXED = ("F-2030", "USD", 1_000_000_000, 4.0, "2025-05-01", "2030-05-15", "fixed", "UST", None)
FIXED_EUR = ("E-2029", "EUR", 1_000_000_000, 2.5, "2025-05-06", "2029-05-06", "fixed", "DBR", None)
FRN = ("N-2028", "USD", 500_000_000, None, "2025-11-06", "2028-11-15", "floating",
       "Compounded SOFR, reset quarterly, plus 0.52% per annum", 52)


def yields(*rows):
    return pd.DataFrame(list(rows) or [("USD", 5.05, "refi", "")],
                        columns=["currency", "refi_yield_pct", "rate_type", "index_name"])


def load_rows(tmp_path, rows, y=None, as_of=AS_OF):
    path = tmp_path / "t.csv"
    pd.DataFrame(rows, columns=COLS).to_csv(path, index=False)
    return treasury.load(path, as_of, fx=FX, yields=y if y is not None else yields())


def test_market_yields_file_marks_usd_refi():
    y = treasury.read_market_yields()
    usd = y[y["currency"] == "USD"]
    assert list(usd["rate_type"]) == ["refi"]
    assert list(usd["refi_yield_pct"]) == [5.05]
    assert "index_name" in y.columns


def test_missing_refi_row_skips_currency_without_nan(tmp_path):
    d = load_rows(tmp_path, [FIXED, FIXED_EUR])
    assert treasury.refi_unavailable(d) == ["EUR"]
    refi = treasury.refi_sensitivity(d, AS_OF, 5, [0, 100])
    assert not refi.isna().any().any()
    assert (refi["principal_refinanced_usd"] == 1_000_000_000).all()  # USD 2030 only


def test_main_prints_refi_unavailable_and_no_nan(tmp_path, monkeypatch, capsys):
    data, out = tmp_path / "data", tmp_path / "output"
    data.mkdir()
    pd.DataFrame([FIXED, FIXED_EUR], columns=COLS).to_csv(data / "tranches.csv", index=False)
    FX.rename("usd_per_unit").rename_axis("currency").reset_index().to_csv(data / "fx_rates.csv", index=False)
    yields().to_csv(data / "market_yields.csv", index=False)
    monkeypatch.setattr(treasury, "DATA", data)
    monkeypatch.setattr(treasury, "OUT", out)
    monkeypatch.setattr(sys, "argv", ["treasury.py", "--as-of", "2026-09-18"])
    treasury.main()
    printed = capsys.readouterr().out
    assert "refi analysis unavailable for EUR: no market yield" in printed
    assert not re.search(r"\bnan\b", printed, re.IGNORECASE)
    assert not re.search(r"\bnan\b", (out / "refi_sensitivity.csv").read_text(), re.IGNORECASE)


def test_frn_costed_from_index_plus_margin(tmp_path):
    d = load_rows(tmp_path, [FIXED, FRN], yields(("USD", 5.05, "refi", ""), ("USD", 4.30, "index", "SOFR")))
    frn = d.set_index("tranche_id").loc["N-2028"]
    assert frn["cost_pct"] == pytest.approx(4.30 + 0.52)
    s = treasury.summary(d)
    assert s["uncosted_frn_usd"] == 0
    assert s["wa_coupon_pct"] == pytest.approx((1000 * 4.0 + 500 * 4.82) / 1500)


def test_frn_without_index_row_is_excluded(tmp_path):
    d = load_rows(tmp_path, [FIXED, FRN])
    s = treasury.summary(d)
    assert s["uncosted_frn_usd"] == 500_000_000
    assert s["wa_coupon_pct"] == pytest.approx(4.0)
    assert s["annual_coupon_usd"] == pytest.approx(40_000_000)
    assert not treasury.refi_sensitivity(d, AS_OF, 5, [0]).isna().any().any()


def test_refi_row_never_used_as_index(tmp_path):
    # A refi row, even one labelled SOFR, must not cost a floating note.
    d = load_rows(tmp_path, [FIXED, FRN], yields(("USD", 5.05, "refi", "SOFR")))
    assert treasury.summary(d)["uncosted_frn_usd"] == 500_000_000


def test_no_outstanding_tranches_stops(tmp_path):
    with pytest.raises(SystemExit, match="No outstanding tranches"):
        load_rows(tmp_path, [FIXED], as_of=pd.Timestamp("2031-01-01"))


def test_fixed_note_without_coupon_stops(tmp_path):
    bad = FIXED[:3] + (None,) + FIXED[4:]
    with pytest.raises(SystemExit, match="blank coupon_pct"):
        load_rows(tmp_path, [bad])
