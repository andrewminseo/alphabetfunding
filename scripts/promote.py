"""
promote.py

Moves reviewed rows from data/tranches_pending.csv into data/tranches.csv.
A row moves only if approved == "yes" and status != "FAIL". Only the 16
tranches.csv columns are kept. Rows already in tranches.csv (same tranche_id,
or same currency + maturity_date + coupon) are skipped and left in pending.

Usage:
    python scripts/promote.py            # promote
    python scripts/promote.py --dry-run  # show what would move
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_terms import BASE_COLS, PENDING, TRANCHES, dedup_key  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not PENDING.exists():
        sys.exit(f"No {PENDING.name}.")
    pending = pd.read_csv(PENDING, dtype=str).fillna("")
    db = pd.read_csv(TRANCHES, dtype=str).fillna("")

    approved = pending["approved"].str.strip().str.lower() == "yes"
    passing = pending["status"].str.strip().str.upper() != "FAIL"
    blocked = pending[approved & ~passing]
    for _, r in blocked.iterrows():
        print(f"  blocked (FAIL): {r['tranche_id']}")

    ids = set(db["tranche_id"])
    keys = {dedup_key(r.currency, r.maturity_date, r.coupon_pct) for r in db.itertuples()}
    moved, dupes = [], []
    for i, r in pending[approved & passing].iterrows():
        key = dedup_key(r["currency"], r["maturity_date"], r["coupon_pct"])
        if not r["tranche_id"] or r["tranche_id"] in ids or key in keys:
            dupes.append(r["tranche_id"])
            continue
        moved.append(i)
        ids.add(r["tranche_id"])
        keys.add(key)

    for t in dupes:
        print(f"  skipped (already in {TRANCHES.name} or no id): {t}")
    for i in moved:
        print(f"  promote: {pending.at[i, 'tranche_id']}")

    if args.dry_run or not moved:
        print(f"{len(moved)} rows {'would move' if args.dry_run else 'moved'}.")
        return
    out = pd.concat([db, pending.loc[moved, BASE_COLS]], ignore_index=True)
    out[BASE_COLS].to_csv(TRANCHES, index=False)
    pending.drop(index=moved).to_csv(PENDING, index=False)
    print(f"{len(moved)} rows moved to {TRANCHES.name}; {len(pending) - len(moved)} left in {PENDING.name}.")


if __name__ == "__main__":
    main()
