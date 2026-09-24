"""
run_pipeline.py

Runs the full refresh in order and stops at the first error:

    edgar_pull.py -> extract_terms.py --index -> update_fx.py
    -> update_yields.py -> treasury.py -> reconciliation

Never runs promote.py: new tranches stay in data/tranches_pending.csv until
you review and promote them yourself. Ends with a summary of new filings, new
pending rows by status, rows needing review, and the reconciliation gap.

Usage:
    python scripts/run_pipeline.py
"""

import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
DATA = ROOT / "data"
INDEX = DATA / "filings_index.csv"
PENDING = DATA / "tranches_pending.csv"

sys.path.insert(0, str(SCRIPTS))
from extract_terms import DEFAULT_MODEL  # noqa: E402
from reconcile import load_and_reconcile, print_report  # noqa: E402

STEPS = [
    ["edgar_pull.py"],
    ["extract_terms.py", "--index", "--model", DEFAULT_MODEL],
    ["update_fx.py"],
    ["update_yields.py"],
    ["treasury.py"],
]


def read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str).fillna("") if path.exists() else pd.DataFrame()


def row_keys(df: pd.DataFrame) -> set:
    if df.empty:
        return set()
    return set(zip(df["source_accession"], df["tranche_id"], df["maturity_date"], df["coupon_pct"]))


def run(step: list[str]) -> None:
    print(f"\n=== {' '.join(step)} ===", flush=True)
    result = subprocess.run([sys.executable, str(SCRIPTS / step[0]), *step[1:]], cwd=ROOT)
    if result.returncode != 0:
        sys.exit(f"\nPipeline stopped: {' '.join(step)} exited with status {result.returncode}. "
                 "Later steps were not run.")


def main() -> None:
    index_before = read(INDEX)
    pending_before = read(PENDING)

    for step in STEPS:
        run(step)

    print("\n=== reconciliation ===")
    try:
        recon = load_and_reconcile(pd.Timestamp.today().normalize())
    except (ValueError, FileNotFoundError, KeyError) as e:
        sys.exit(f"\nPipeline stopped: reconciliation failed: {e}")
    print_report(recon)

    index_after = read(INDEX)
    pending_after = read(PENDING)
    before_acc = set(index_before.get("accessionNumber", []))
    new_filings = index_after[~index_after["accessionNumber"].isin(before_acc)]
    new_keys = row_keys(pending_after) - row_keys(pending_before)
    new_rows = pending_after[[k in new_keys for k in zip(
        pending_after.get("source_accession", []), pending_after.get("tranche_id", []),
        pending_after.get("maturity_date", []), pending_after.get("coupon_pct", []))]] \
        if not pending_after.empty else pending_after
    unreviewed = pending_after[pending_after["approved"].str.strip() == ""] \
        if not pending_after.empty else pending_after

    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(f"New filings found: {len(new_filings)}")
    for _, f in new_filings.iterrows():
        print(f"  {f['filingDate']}  {f['form']:<6} {f['accessionNumber']}")

    print(f"\nNew pending rows: {len(new_rows)}")
    if len(new_rows):
        for status, n in new_rows["status"].value_counts().items():
            print(f"  {status}: {n}")

    print(f"\nNeeding your review (approved blank in {PENDING.name}): {len(unreviewed)}")
    if len(unreviewed):
        for status, n in unreviewed["status"].value_counts().items():
            print(f"  {status}: {n}")
        flagged = unreviewed[unreviewed["status"].isin(["FAIL", "WARN"])]
        for _, r in flagged.iterrows():
            print(f"  {r['status']:<4} {r['tranche_id']}: {r['issues']}")

    print(f"\nReconciliation gap: {recon['gap_usd'] / 1e9:+,.2f}B ({recon['gap_pct']:+.1f}%) "
          f"vs LongTermDebt at {recon['reported_period_end']}")
    print("\npromote.py was not run.")


if __name__ == "__main__":
    main()
