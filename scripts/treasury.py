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
    "issue_date",
    "maturity_date",
    "rate_type",
]
# coupon_pct is required for fixed notes only; floating notes have no fixed coupon.


def read_market_yields(path: Path | None = None) -> pd.DataFrame:
    """market_yields.csv: one row per rate. rate_type "refi" is the assumed
    refinancing yield for a currency; rate_type "index" is a floating index
    level (index_name, e.g. SOFR). The value is in refi_yield_pct for both."""
    y = pd.read_csv(path or DATA / "market_yields.csv")
    y["currency"] = y["currency"].str.upper().str.strip()
    y["rate_type"] = y["rate_type"].str.lower().str.strip()
    y["index_name"] = y["index_name"].fillna("").astype(str).str.strip()
    return y


def refi_yields(y: pd.DataFrame) -> pd.Series:
    refi = y[y["rate_type"] == "refi"]
    if refi["currency"].duplicated().any():
        sys.exit("market_yields.csv: more than one refi row for a currency")
    return refi.set_index("currency")["refi_yield_pct"].dropna()


def index_rate(y: pd.DataFrame, tranche: pd.Series) -> float | None:
    """The index row for a floating note: same currency, and index_name named in
    the tranche's floating_index (if present) or benchmark text. Never a refi row."""
    rows = y[(y["rate_type"] == "index") & (y["currency"] == tranche["currency"])]
    names = " ".join(str(tranche.get(c, "") or "") for c in ("floating_index", "benchmark")).lower()
    hits = rows[[bool(n) and n.lower() in names for n in rows["index_name"]]]
    if len(hits) != 1 or pd.isna(hits["refi_yield_pct"].iloc[0]):
        return None
    return float(hits["refi_yield_pct"].iloc[0])


def load(tranche_file: Path, as_of: pd.Timestamp,
         fx: pd.Series | None = None, yields: pd.DataFrame | None = None) -> pd.DataFrame:
    t = pd.read_csv(tranche_file)

    if t.empty:
        sys.exit(f"{tranche_file.name} has no rows.")

    missing = [c for c in REQUIRED if c not in t.columns]
    if missing:
        sys.exit(f"Missing columns: {missing}")

    t["issue_date"] = pd.to_datetime(t["issue_date"])
    t["maturity_date"] = pd.to_datetime(t["maturity_date"])
    t["currency"] = t["currency"].str.upper().str.strip()
    t["rate_type"] = t["rate_type"].str.lower().str.strip()
    if "coupon_pct" not in t.columns:
        t["coupon_pct"] = float("nan")
    floating = t["rate_type"] == "floating"

    problems = []

    if t["tranche_id"].duplicated().any():
        problems.append("duplicate tranche_id")

    if (t["maturity_date"] <= t["issue_date"]).any():
        problems.append("maturity date must be after issue date")

    if t[REQUIRED].isna().any().any():
        problems.append("blank required fields")

    if t.loc[~floating, "coupon_pct"].isna().any():
        problems.append("blank coupon_pct on a fixed-rate note")

    if problems:
        sys.exit("Data problems:\n" + "\n".join(problems))

    if fx is None:
        fx = pd.read_csv(DATA / "fx_rates.csv").set_index("currency")["usd_per_unit"]
    if yields is None:
        yields = read_market_yields()

    missing_fx = sorted(set(t["currency"]) - set(fx.index))
    if missing_fx:
        sys.exit(f"Missing FX rates for: {missing_fx}")

    live = t[
        (t["issue_date"] <= as_of) &
        (t["maturity_date"] > as_of)
    ].copy()

    if live.empty:
        sys.exit(f"No outstanding tranches as of {as_of:%Y-%m-%d} in {tranche_file.name}.")

    live["principal_usd"] = live["principal_local"] * live["currency"].map(fx)
    live["years_to_maturity"] = (
        (live["maturity_date"] - as_of).dt.days / 365.25
    )

    # Current cost: fixed coupon, or index + margin for floating notes.
    # Floating notes without an index row or margin stay NaN and are excluded.
    margin = live["floating_margin_bps"] if "floating_margin_bps" in live.columns else pd.Series(float("nan"), index=live.index)
    live["cost_pct"] = live["coupon_pct"].where(live["rate_type"] != "floating")
    for i, r in live[live["rate_type"] == "floating"].iterrows():
        rate = index_rate(yields, r)
        if rate is not None and pd.notna(margin[i]):
            live.at[i, "cost_pct"] = rate + float(margin[i]) / 100
    live["costed"] = live["cost_pct"].notna()

    live["annual_coupon_usd"] = (
        live["principal_usd"] * live["cost_pct"] / 100
    )
    live["refi_yield_pct"] = live["currency"].map(refi_yields(yields))

    return live


def uncosted_frn_usd(d: pd.DataFrame) -> float:
    return float(d.loc[~d["costed"], "principal_usd"].sum())


def refi_unavailable(d: pd.DataFrame) -> list[str]:
    return sorted(d.loc[d["refi_yield_pct"].isna(), "currency"].unique())


def summary(d: pd.DataFrame) -> dict:
    total = d["principal_usd"].sum()
    c = d[d["costed"]]
    costed_total = c["principal_usd"].sum()

    return {
        "outstanding_usd": total,
        "n_tranches": len(d),
        "wa_coupon_pct": (
            (c["cost_pct"] * c["principal_usd"]).sum() / costed_total
            if costed_total else float("nan")
        ),
        "wa_maturity_yrs": (
            d["years_to_maturity"] * d["principal_usd"]
        ).sum() / total,
        "annual_coupon_usd": c["annual_coupon_usd"].sum(),
        "uncosted_frn_usd": uncosted_frn_usd(d),
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
    # Only notes with both a current cost and a refi yield for their currency.
    due = d[
        (d["maturity_date"] <= cutoff)
        & d["costed"]
        & d["refi_yield_pct"].notna()
    ]

    rows = []

    for shock in shocks_bps:
        new_rate = due["refi_yield_pct"] + shock / 100

        delta = (
            due["principal_usd"]
            * (new_rate - due["cost_pct"])
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

    # Plot only years with maturities; the ladder itself keeps every year.
    # Bars are evenly spaced, so gaps between years are not to scale.
    due = ladder[ladder["TOTAL"] > 0]

    ax = (
        due[currencies] / 1e9
    ).plot(
        kind="bar",
        stacked=True,
        figsize=(max(11, 0.45 * len(due)), 5.5),
        width=0.8,
    )

    ax.set(
        title=title,
        xlabel="Maturity year (years with maturities only)",
        ylabel="Principal due (USD billions)",
    )
    ax.tick_params(axis="x", labelrotation=45, labelsize=9)
    plt.setp(ax.get_xticklabels(), ha="right", rotation_mode="anchor")

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
    if pd.notna(stats["wa_coupon_pct"]):
        print(f"{'Weighted avg coupon':<28}{stats['wa_coupon_pct']:>11.2f}%")
    else:
        print(f"{'Weighted avg coupon':<28}{'n/a':>12}")
    print(f"{'Weighted avg maturity':<28}{stats['wa_maturity_yrs']:>9.1f} yrs")
    print(f"{'Annual coupon cost':<28}{fmt_b(stats['annual_coupon_usd']):>12}")
    print(f"{'Maturing next 5 yrs':<28}{fmt_b(due_5y):>12}")
    if stats["uncosted_frn_usd"]:
        print(f"\nFloating-rate notes excluded from coupon cost and avg coupon "
              f"(no index rate or margin): {fmt_b(stats['uncosted_frn_usd'])}")

    print("\nCurrency mix")
    for currency, weight in stats["currency_mix"].items():
        print(f"  {currency:<26}{weight:>11.1%}")

    print(f"\nRefinancing sensitivity ({args.horizon}Y)")
    for ccy in refi_unavailable(debt):
        print(f"  refi analysis unavailable for {ccy}: no market yield")
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