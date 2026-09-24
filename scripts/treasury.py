"""
Treasury analytics for Alphabet's debt portfolio.

Outputs:
- Debt, coupon, maturity, currency, and refinancing summary
- Maturity ladder
- Refinancing sensitivity
"""

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "output"

REQUIRED = [
    "tranche_id",
    "currency",
    "principal_local",
    "coupon_pct",
    "issue_date",
    "maturity_date",
    "rate_type",
]


def load(tranche_file: Path, as_of: pd.Timestamp) -> pd.DataFrame:
    t = pd.read_csv(tranche_file)

    if t.empty:
        sys.exit(f"{tranche_file.name} has no rows.")

    missing = [c for c in REQUIRED if c not in t.columns]
    if missing:
        sys.exit(f"Missing columns: {missing}")

    t["issue_date"] = pd.to_datetime(t["issue_date"])
    t["maturity_date"] = pd.to_datetime(t["maturity_date"])
    t["currency"] = t["currency"].str.upper().str.strip()

    problems = []

    if t["tranche_id"].duplicated().any():
        problems.append("duplicate tranche_id")

    if (t["maturity_date"] <= t["issue_date"]).any():
        problems.append("maturity date must be after issue date")

    if t[REQUIRED].isna().any().any():
        problems.append("blank required fields")

    if problems:
        sys.exit("Data problems:\n" + "\n".join(problems))

    fx = pd.read_csv(DATA / "fx_rates.csv").set_index("currency")["usd_per_unit"]
    yields = pd.read_csv(DATA / "market_yields.csv").set_index("currency")["refi_yield_pct"]

    missing_fx = sorted(set(t["currency"]) - set(fx.index))
    if missing_fx:
        sys.exit(f"Missing FX rates for: {missing_fx}")

    live = t[
        (t["issue_date"] <= as_of) &
        (t["maturity_date"] > as_of)
    ].copy()

    live["principal_usd"] = live["principal_local"] * live["currency"].map(fx)
    live["years_to_maturity"] = (
        (live["maturity_date"] - as_of).dt.days / 365.25
    )
    live["annual_coupon_usd"] = (
        live["principal_usd"] * live["coupon_pct"] / 100
    )
    live["refi_yield_pct"] = live["currency"].map(yields)

    return live


def summary(d: pd.DataFrame) -> dict:
    total = d["principal_usd"].sum()

    return {
        "outstanding_usd": total,
        "n_tranches": len(d),
        "wa_coupon_pct": (
            d["coupon_pct"] * d["principal_usd"]
        ).sum() / total,
        "wa_maturity_yrs": (
            d["years_to_maturity"] * d["principal_usd"]
        ).sum() / total,
        "annual_coupon_usd": d["annual_coupon_usd"].sum(),
        "currency_mix": (
            d.groupby("currency")["principal_usd"].sum() / total
        ).sort_values(ascending=False),
    }


def maturity_ladder(d: pd.DataFrame) -> pd.DataFrame:
    ladder = (
        d.assign(year=d["maturity_date"].dt.year)
        .pivot_table(
            index="year",
            columns="currency",
            values="principal_usd",
            aggfunc="sum",
            fill_value=0,
        )
    )

    ladder = ladder.reindex(
        range(ladder.index.min(), ladder.index.max() + 1),
        fill_value=0,
    )

    ladder["TOTAL"] = ladder.sum(axis=1)

    return ladder


def refi_sensitivity(
    d: pd.DataFrame,
    as_of: pd.Timestamp,
    horizon: int,
    shocks_bps: list[int],
) -> pd.DataFrame:

    cutoff = as_of + pd.DateOffset(years=horizon)
    due = d[d["maturity_date"] <= cutoff]

    rows = []

    for shock in shocks_bps:
        new_rate = due["refi_yield_pct"] + shock / 100

        delta = (
            due["principal_usd"]
            * (new_rate - due["coupon_pct"])
            / 100
        ).sum()

        rows.append({
            "shock_bps": shock,
            "principal_refinanced_usd": due["principal_usd"].sum(),
            "delta_annual_interest_usd": delta,
        })

    return pd.DataFrame(rows)


def plot_ladder(
    ladder: pd.DataFrame,
    path: Path,
    title: str,
) -> None:

    currencies = [c for c in ladder.columns if c != "TOTAL"]

    ax = (
        ladder[currencies] / 1e9
    ).plot(
        kind="bar",
        stacked=True,
        figsize=(11, 5),
        width=0.8,
    )

    ax.set(
        title=title,
        xlabel="Maturity year",
        ylabel="Principal due (USD billions)",
    )

    ax.legend(title="Currency", frameon=False)
    ax.spines[["top", "right"]].set_visible(False)

    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()


def fmt_b(value: float) -> str:
    return f"${value / 1e9:,.1f}B"


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--as-of",
        default=pd.Timestamp.today().strftime("%Y-%m-%d"),
    )
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument(
        "--shocks",
        type=int,
        nargs="+",
        default=[-100, 0, 100, 200],
    )

    args = parser.parse_args()

    as_of = pd.Timestamp(args.as_of)
    source = DATA / "tranches.csv"

    debt = load(source, as_of)

    OUT.mkdir(exist_ok=True)

    stats = summary(debt)
    ladder = maturity_ladder(debt)
    refi = refi_sensitivity(
        debt,
        as_of,
        args.horizon,
        args.shocks,
    )

    due_5y = debt[
        debt["maturity_date"]
        <= as_of + pd.DateOffset(years=5)
    ]["principal_usd"].sum()

    print("\nALPHABET TREASURY MONITOR")
    print(f"As of {as_of:%B %d, %Y}\n")

    print(f"{'Debt tracked':<28}{fmt_b(stats['outstanding_usd']):>12}")
    print(f"{'Tranches':<28}{stats['n_tranches']:>12}")
    print(f"{'Weighted avg coupon':<28}{stats['wa_coupon_pct']:>11.2f}%")
    print(f"{'Weighted avg maturity':<28}{stats['wa_maturity_yrs']:>9.1f} yrs")
    print(f"{'Annual coupon cost':<28}{fmt_b(stats['annual_coupon_usd']):>12}")
    print(f"{'Maturing next 5 yrs':<28}{fmt_b(due_5y):>12}")

    print("\nCurrency mix")
    for currency, weight in stats["currency_mix"].items():
        print(f"  {currency:<26}{weight:>11.1%}")

    print(f"\nRefinancing sensitivity ({args.horizon}Y)")
    for _, row in refi.iterrows():
        print(
            f"  {int(row['shock_bps']):+5d} bps"
            f"{row['delta_annual_interest_usd'] / 1e6:>18,.0f}M / yr"
        )

    ladder.to_csv(OUT / "maturity_ladder.csv")
    refi.to_csv(OUT / "refi_sensitivity.csv", index=False)

    plot_ladder(
        ladder,
        OUT / "maturity_ladder.png",
        f"Alphabet debt maturity ladder, {as_of:%Y-%m-%d}",
    )

    print("\nOutputs saved to /output")


if __name__ == "__main__":
    main()