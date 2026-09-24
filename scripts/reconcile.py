"""
reconcile.py

Compares the face value of tranches in data/tranches.csv that were outstanding
at the period end of the latest reported LongTermDebt (data/xbrl_debt_facts.csv)
to that reported figure, converted to USD with data/fx_rates.csv. Tranches
issued after the period end (and not matured as of --as-of) are reported
separately, since they cannot be in the reported figure yet.

Face value and reported (carrying) value differ by discounts and issuance
costs, and FX uses today's snapshot, so some gap is expected.

Usage:
    python scripts/reconcile.py [--as-of YYYY-MM-DD]
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def to_usd(tranches: pd.DataFrame, fx: pd.Series) -> pd.DataFrame:
    t = tranches.copy()
    t["currency"] = t["currency"].str.upper().str.strip()
    t["issue_date"] = pd.to_datetime(t["issue_date"])
    t["maturity_date"] = pd.to_datetime(t["maturity_date"])
    missing = sorted(set(t["currency"]) - set(fx.index))
    if missing:
        raise ValueError(f"missing FX rates for {missing}")
    t["principal_usd"] = t["principal_local"].astype(float) * t["currency"].map(fx)
    return t


def outstanding_at(t: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
    """Tranches outstanding on `date`: issued on or before it, maturing after it."""
    return t[(t["issue_date"] <= date) & (t["maturity_date"] > date)]


def issued_after(t: pd.DataFrame, period_end: pd.Timestamp, as_of: pd.Timestamp) -> pd.DataFrame:
    """Issued after the period end and still outstanding as of `as_of`."""
    return t[(t["issue_date"] > period_end) & (t["maturity_date"] > as_of)]


def latest_long_term_debt(facts: pd.DataFrame) -> pd.Series:
    ltd = facts[(facts["tag"] == "LongTermDebt") & (facts["unit"] == "USD")]
    if ltd.empty:
        raise ValueError("no LongTermDebt facts")
    return ltd.sort_values(["period_end", "filed"]).iloc[-1]


def reconcile(tranches: pd.DataFrame, fx: pd.Series, facts: pd.DataFrame,
              as_of: pd.Timestamp) -> dict:
    t = to_usd(tranches, fx)
    reported = latest_long_term_debt(facts)
    period_end = pd.Timestamp(reported["period_end"])
    live = outstanding_at(t, period_end)
    after = issued_after(t, period_end, as_of)
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
        "issued_after_period_end_usd": float(after["principal_usd"].sum()),
        "n_issued_after": len(after),
    }


def load_and_reconcile(as_of: pd.Timestamp) -> dict:
    tranches = pd.read_csv(DATA / "tranches.csv")
    fx = pd.read_csv(DATA / "fx_rates.csv").set_index("currency")["usd_per_unit"]
    facts = pd.read_csv(DATA / "xbrl_debt_facts.csv")
    return reconcile(tranches, fx, facts, as_of)


def print_report(r: dict) -> None:
    print("\nRECONCILIATION")
    print(f"  Tranche face value (USD, {r['n_tranches']} outstanding at {r['reported_period_end']}): "
          f"${r['tranche_face_usd'] / 1e9:,.2f}B")
    print(f"  Reported LongTermDebt ({r['reported_period_end']}, {r['reported_accession']}): "
          f"${r['reported_ltd_usd'] / 1e9:,.2f}B")
    print(f"  Gap: {r['gap_usd'] / 1e9:+,.2f}B ({r['gap_pct']:+.1f}%)")
    print(f"  Issued after {r['reported_period_end']} and outstanding at {r['as_of']} "
          f"({r['n_issued_after']} tranches, not in the gap): "
          f"${r['issued_after_period_end_usd'] / 1e9:,.2f}B")


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
