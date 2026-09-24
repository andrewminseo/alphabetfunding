"""
reconcile.py

Compares total outstanding face value in data/tranches.csv (converted to USD
with data/fx_rates.csv, excluding notes matured as of the given date) to the
latest LongTermDebt reported in data/xbrl_debt_facts.csv.

Face value and reported (carrying) value differ by discounts and issuance
costs, and the XBRL figure is as of its period end, so some gap is expected.

Usage:
    python scripts/reconcile.py [--as-of YYYY-MM-DD]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def outstanding_usd(tranches: pd.DataFrame, fx: pd.Series, as_of: pd.Timestamp) -> pd.DataFrame:
    t = tranches.copy()
    t["currency"] = t["currency"].str.upper().str.strip()
    t["maturity_date"] = pd.to_datetime(t["maturity_date"])
    t = t[t["maturity_date"] > as_of]
    missing = sorted(set(t["currency"]) - set(fx.index))
    if missing:
        raise ValueError(f"missing FX rates for {missing}")
    t["principal_usd"] = t["principal_local"].astype(float) * t["currency"].map(fx)
    return t


def latest_long_term_debt(facts: pd.DataFrame) -> pd.Series:
    ltd = facts[(facts["tag"] == "LongTermDebt") & (facts["unit"] == "USD")]
    if ltd.empty:
        raise ValueError("no LongTermDebt facts")
    return ltd.sort_values(["period_end", "filed"]).iloc[-1]


def reconcile(tranches: pd.DataFrame, fx: pd.Series, facts: pd.DataFrame,
              as_of: pd.Timestamp) -> dict:
    live = outstanding_usd(tranches, fx, as_of)
    reported = latest_long_term_debt(facts)
    total = float(live["principal_usd"].sum())
    value = float(reported["value"])
    gap = total - value
    return {
        "as_of": as_of.date().isoformat(),
        "n_tranches": len(live),
        "tranche_face_usd": total,
        "reported_ltd_usd": value,
        "reported_period_end": reported["period_end"],
        "reported_accession": reported["accession"],
        "gap_usd": gap,
        "gap_pct": gap / value * 100,
        "issued_after_period_end_usd": float(
            live.loc[pd.to_datetime(live["issue_date"]) > pd.Timestamp(reported["period_end"]),
                     "principal_usd"].sum()),
    }


def load_and_reconcile(as_of: pd.Timestamp) -> dict:
    tranches = pd.read_csv(DATA / "tranches.csv")
    fx = pd.read_csv(DATA / "fx_rates.csv").set_index("currency")["usd_per_unit"]
    facts = pd.read_csv(DATA / "xbrl_debt_facts.csv")
    return reconcile(tranches, fx, facts, as_of)


def print_report(r: dict) -> None:
    print("\nRECONCILIATION")
    print(f"  Tranche face value (USD, {r['n_tranches']} live as of {r['as_of']}): "
          f"${r['tranche_face_usd'] / 1e9:,.2f}B")
    print(f"  Reported LongTermDebt ({r['reported_period_end']}, {r['reported_accession']}): "
          f"${r['reported_ltd_usd'] / 1e9:,.2f}B")
    print(f"  Gap: {r['gap_usd'] / 1e9:+,.2f}B ({r['gap_pct']:+.1f}%)")
    if r["issued_after_period_end_usd"]:
        print(f"  Of tranche total, issued after {r['reported_period_end']}: "
              f"${r['issued_after_period_end_usd'] / 1e9:,.2f}B (not yet in reported figure)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--as-of", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    args = p.parse_args()
    try:
        r = load_and_reconcile(pd.Timestamp(args.as_of))
    except (ValueError, FileNotFoundError, KeyError) as e:
        sys.exit(f"Reconciliation failed: {e}")
    print_report(r)


if __name__ == "__main__":
    main()
