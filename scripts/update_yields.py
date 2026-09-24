"""
update_yields.py

Downloads the U.S. Treasury Daily Par Yield Curve Rates for the latest
available date and writes one row per tenor to data/treasury_curve.csv:
observation_date, tenor, value, source, source_url.

If the download fails, the existing file is left unchanged and the script
exits with status 1.
"""

import io
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "treasury_curve.csv"

URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
       "daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve"
       "&field_tdr_date_value={year}&page&_format=csv")
SOURCE = "U.S. Treasury Daily Par Yield Curve Rates"


def fetch_year(year: int) -> tuple[pd.DataFrame, str]:
    url = URL.format(year=year)
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    if df.empty or "Date" not in df.columns:
        raise ValueError(f"no curve data for {year}")
    return df, url


def latest_curve() -> pd.DataFrame:
    year = date.today().year
    try:
        df, url = fetch_year(year)
    except ValueError:  # early January: nothing published yet this year
        df, url = fetch_year(year - 1)
    df["Date"] = pd.to_datetime(df["Date"], format="%m/%d/%Y")
    row = df.loc[df["Date"].idxmax()]
    tenors = [c for c in df.columns if c != "Date" and pd.notna(row[c])]
    return pd.DataFrame({
        "observation_date": row["Date"].date().isoformat(),
        "tenor": tenors,
        "value": [float(row[c]) for c in tenors],
        "source": SOURCE,
        "source_url": url,
    })


def main() -> None:
    try:
        curve = latest_curve()
    except Exception as e:  # network, HTTP, or parse failure
        print(f"Treasury curve download failed ({e}); {OUTPUT.name} left unchanged.")
        sys.exit(1)
    tmp = OUTPUT.with_suffix(".tmp")
    curve.to_csv(tmp, index=False)
    tmp.replace(OUTPUT)
    print(f"Treasury par curve {curve['observation_date'].iloc[0]}: {len(curve)} tenors -> "
          f"data/{OUTPUT.name}")
    print(curve[["tenor", "value"]].to_string(index=False))


if __name__ == "__main__":
    main()
