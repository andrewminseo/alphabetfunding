"""treasury.py analytics on the four verified April 2025 USD tranches."""

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
