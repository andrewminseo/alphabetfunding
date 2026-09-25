import argparse
from pathlib import Path

import pandas as pd
import requests


API = "https://api.frankfurter.dev/v2/rates"
CURRENCIES = ["EUR", "GBP", "CHF", "JPY", "CAD"]

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "fx_rates.csv"
HISTORY = ROOT / "data" / "fx_history.csv"
HISTORY_API = "https://api.frankfurter.dev/v1/{date}"


def get_fx_rates():
    response = requests.get(
        API,
        params={
            "base": "USD",
            "quotes": ",".join(CURRENCIES),
        },
        timeout=10,
    )
    response.raise_for_status()

    data = response.json()

    rows = [{
        "currency": "USD",
        "usd_per_unit": 1.0,
        "as_of": data[0]["date"],
        "source": "Identity",
    }]

    for item in data:
        currency = item["quote"]
        usd_per_currency = 1 / item["rate"]

        rows.append({
            "currency": currency,
            "usd_per_unit": round(usd_per_currency, 6),
            "as_of": item["date"],
            "source": "Frankfurter v2",
        })

    return pd.DataFrame(rows)


def get_fx_rates_on(date: str) -> pd.DataFrame:
    """USD per unit on a past date (ECB reference rates via Frankfurter; if the
    date is not a publication day, the latest earlier rate is returned)."""
    response = requests.get(
        HISTORY_API.format(date=date),
        params={"base": "USD", "symbols": ",".join(CURRENCIES)},
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    rows = [{"date": date, "currency": "USD", "usd_per_unit": 1.0,
             "rate_date": data["date"], "source": "Identity"}]
    for currency, rate in data["rates"].items():
        rows.append({"date": date, "currency": currency,
                     "usd_per_unit": round(1 / rate, 6), "rate_date": data["date"],
                     "source": "Frankfurter v1 (ECB reference rate)"})
    return pd.DataFrame(rows)


def save_history(date: str) -> pd.DataFrame:
    """Add (or replace) the rates for `date` in data/fx_history.csv."""
    new = get_fx_rates_on(date)
    if HISTORY.exists():
        old = pd.read_csv(HISTORY, dtype={"date": str})
        new = pd.concat([old[old["date"] != date], new], ignore_index=True)
    new = new.sort_values(["date", "currency"]).reset_index(drop=True)
    new.to_csv(HISTORY, index=False)
    return new[new["date"] == date]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="save rates for a past date (YYYY-MM-DD) "
                        "to data/fx_history.csv instead of refreshing fx_rates.csv")
    args = parser.parse_args()

    if args.date:
        fx = save_history(args.date)
        print(f"\nFX RATES FOR {args.date}")
        print(fx.to_string(index=False))
        print(f"\nSaved: {HISTORY}")
        return

    fx = get_fx_rates()
    fx.to_csv(OUTPUT, index=False)

    print("\nFX RATES UPDATED")
    print(fx.to_string(index=False))
    print(f"\nSaved: {OUTPUT}")


if __name__ == "__main__":
    main()