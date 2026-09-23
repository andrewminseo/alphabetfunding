from pathlib import Path

import pandas as pd
import requests


API = "https://api.frankfurter.dev/v2/rates"
CURRENCIES = ["EUR", "GBP", "CHF", "JPY", "CAD"]

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "fx_rates.csv"


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


def main():
    fx = get_fx_rates()
    fx.to_csv(OUTPUT, index=False)

    print("\nFX RATES UPDATED")
    print(fx.to_string(index=False))
    print(f"\nSaved: {OUTPUT}")


if __name__ == "__main__":
    main()