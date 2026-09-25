"""
reconcile.py

Compares the face value of tranches in data/tranches.csv that were outstanding
at the period end of the latest reported LongTermDebt (data/xbrl_debt_facts.csv)
to that reported figure, converted to USD. Tranches
issued after the period end (and not matured as of --as-of) are reported
separately, since they cannot be in the reported figure yet.

Non-USD principal is converted at the period-end rate from data/fx_history.csv,
matching how the reported figure is translated. Add a missing date with
    python scripts/update_fx.py --date YYYY-MM-DD

The reported total is XBRL LongTermDebt for the period, or, where a filing
does not tag LongTermDebt (10-Qs), DebtInstrumentCarryingAmount, which Alphabet
uses for the "Total face value of long-term debt" line. When the filing's debt
table has an "Other long-term debt" row (credit facilities), it is reported as
a separate known item and left out of the notes-only gap.

Face value and reported (carrying) value differ by discounts and issuance
costs, and FX uses today's snapshot, so some gap is expected.

Usage:
    python scripts/reconcile.py [--as-of YYYY-MM-DD]
"""

import argparse
import os
import re
import sys
import time
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


REPORTED_TAGS = ["LongTermDebt", "DebtInstrumentCarryingAmount"]  # in order of preference
RAW = DATA / "raw"


def latest_long_term_debt(facts: pd.DataFrame, period_end: str | None = None) -> pd.Series:
    """Reported total debt for `period_end` (default: the latest period with
    either tag), preferring LongTermDebt over DebtInstrumentCarryingAmount."""
    f = facts[facts["tag"].isin(REPORTED_TAGS) & (facts["unit"] == "USD")].copy()
    if f.empty:
        raise ValueError(f"no {' or '.join(REPORTED_TAGS)} facts")
    f["period_end"] = f["period_end"].astype(str).str[:10]
    period_end = period_end or f["period_end"].max()
    f = f[f["period_end"] == period_end]
    if f.empty:
        raise ValueError(f"no reported debt total for {period_end}")
    f["rank"] = f["tag"].map(REPORTED_TAGS.index)
    return f.sort_values(["rank", "filed"], ascending=[True, False]).iloc[0]


def other_long_term_debt(accession: str, index_path: Path = DATA / "filings_index.csv",
                         user_agent: str | None = None) -> tuple[float | None, str]:
    """USD amount on the "Other long-term debt" row of the filing's debt table
    (last column = the filing's own period), and a note on where it came from.
    None if the filing has no such row or can't be read."""
    from bs4 import BeautifulSoup
    idx = pd.read_csv(index_path)
    row = idx[idx["accessionNumber"] == accession]
    if row.empty:
        return None, f"{accession} not in {index_path.name}"
    url, doc = row["url"].iloc[0], row["primaryDocument"].iloc[0]
    cache = RAW / f"{accession}_{doc}"
    if cache.exists():
        html = cache.read_bytes()
    else:
        ua = user_agent or os.environ.get("SEC_USER_AGENT")
        if not ua:
            return None, "filing not cached and SEC_USER_AGENT not set"
        import requests
        r = requests.get(url, headers={"User-Agent": ua}, timeout=60)
        r.raise_for_status()
        time.sleep(0.15)
        RAW.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(r.content)
        html = r.content
    soup = BeautifulSoup(html, "html.parser")
    for tr in soup.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        cells = [c for c in cells if c and c != "$"]
        if cells and re.fullmatch(r"Other long-term debt", cells[0], re.IGNORECASE):
            nums = [c.replace(",", "") for c in cells[1:] if re.fullmatch(r"[\d,]+", c)]
            if nums:
                return float(nums[-1]) * 1e6, f"'Other long-term debt' row, {accession} ($ millions, last column)"
    return None, f"no 'Other long-term debt' row in {accession}"


def reconcile(tranches: pd.DataFrame, fx: pd.Series, facts: pd.DataFrame,
              as_of: pd.Timestamp, period_end: str | None = None,
              other_debt_usd: float | None = None) -> dict:
    """Gap = tranche face value - (reported total - other long-term debt).
    other_debt_usd is the filing's "Other long-term debt" (credit facilities),
    which no note row can match; None if the filing does not give it."""
    t = to_usd(tranches, fx)
    reported = latest_long_term_debt(facts, period_end)
    period_end = pd.Timestamp(reported["period_end"])
    live = outstanding_at(t, period_end)
    after = issued_after(t, period_end, as_of)
    total = float(live["principal_usd"].sum())
    value = float(reported["value"])
    notes_reported = value - (other_debt_usd or 0.0)
    gap = total - notes_reported
    return {
        "reported_tag": reported["tag"],
        "other_debt_usd": other_debt_usd,
        "reported_notes_usd": notes_reported,
        "as_of": as_of.date().isoformat(),
        "n_tranches": len(live),
        "tranche_face_usd": total,
        "reported_ltd_usd": value,
        "reported_period_end": reported["period_end"],
        "reported_accession": reported["accession"],
        "gap_usd": gap,
        "gap_pct": gap / notes_reported * 100,
        "issued_after_period_end_usd": float(after["principal_usd"].sum()),
        "n_issued_after": len(after),
    }


def fx_on(date: str, path: Path = DATA / "fx_history.csv") -> pd.Series:
    """USD per unit on `date` from the FX history file."""
    if not path.exists():
        raise ValueError(f"{path.name} not found; run: python scripts/update_fx.py --date {date}")
    h = pd.read_csv(path, dtype={"date": str})
    rows = h[h["date"] == date]
    if rows.empty:
        raise ValueError(f"no FX rates for {date} in {path.name}; "
                         f"run: python scripts/update_fx.py --date {date}")
    return rows.set_index("currency")["usd_per_unit"]


def load_and_reconcile(as_of: pd.Timestamp, period_end: str | None = None) -> dict:
    tranches = pd.read_csv(DATA / "tranches.csv")
    facts = pd.read_csv(DATA / "xbrl_debt_facts.csv")
    reported = latest_long_term_debt(facts, period_end)
    period_end = str(reported["period_end"])[:10]
    fx = fx_on(period_end)
    other, other_note = other_long_term_debt(reported["accession"])
    r = reconcile(tranches, fx, facts, as_of, period_end, other)
    r["fx_date"] = period_end
    r["other_debt_note"] = other_note
    return r


def print_report(r: dict) -> None:
    print("\nRECONCILIATION")
    print(f"  Tranche face value (USD, {r['n_tranches']} outstanding at {r['reported_period_end']}): "
          f"${r['tranche_face_usd'] / 1e9:,.2f}B")
    print(f"  Reported total ({r['reported_tag']}, {r['reported_period_end']}, {r['reported_accession']}): "
          f"${r['reported_ltd_usd'] / 1e9:,.2f}B")
    if r.get("other_debt_usd"):
        print(f"  Less other long-term debt (credit facilities, known item): "
              f"${r['other_debt_usd'] / 1e9:,.2f}B  [{r.get('other_debt_note', '')}]")
        print(f"  Reported notes: ${r['reported_notes_usd'] / 1e9:,.2f}B")
    elif r.get("other_debt_note"):
        print(f"  Other long-term debt: none reported [{r['other_debt_note']}]")
    print(f"  Gap: {r['gap_usd'] / 1e9:+,.2f}B ({r['gap_pct']:+.1f}%)")
    if r.get("fx_date"):
        print(f"  FX: non-USD principal converted at {r['fx_date']} rates (data/fx_history.csv)")
    print(f"  Issued after {r['reported_period_end']} and outstanding at {r['as_of']} "
          f"({r['n_issued_after']} tranches, not in the gap): "
          f"${r['issued_after_period_end_usd'] / 1e9:,.2f}B")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--as-of", default=pd.Timestamp.today().strftime("%Y-%m-%d"))
    p.add_argument("--period-end", help="reported period to reconcile to (default: latest)")
    args = p.parse_args()
    try:
        r = load_and_reconcile(pd.Timestamp(args.as_of), args.period_end)
    except (ValueError, FileNotFoundError, KeyError) as e:
        sys.exit(f"Reconciliation failed: {e}")
    print_report(r)


if __name__ == "__main__":
    main()
