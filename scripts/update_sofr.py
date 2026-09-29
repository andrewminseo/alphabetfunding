"""
update_sofr.py

Downloads the latest NY Fed 30-day Average SOFR and writes it to the USD SOFR
index row of data/market_yields.csv (refi_yield_pct, as_of, source). Other
rows are left as they are. Floating-rate notes are costed at this rate plus
their margin in treasury.py.

If the download fails or the file has no USD SOFR index row, the file is left
unchanged and the script exits with status 1.
"""

import csv
import io
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "market_yields.csv"

URL = "https://markets.newyorkfed.org/api/rates/secured/sofrai/last/1.json"
PAGE = "https://www.newyorkfed.org/markets/reference-rates/sofr-averages-and-index"


def latest_sofr_average() -> tuple[float, str]:
    """(30-day Average SOFR in percent, effective date) from the NY Fed API."""
    r = requests.get(URL, timeout=30)
    r.raise_for_status()
    rate = r.json()["refRates"][0]
    return float(rate["average30day"]), rate["effectiveDate"]


def set_sofr_row(text: str, value: float, as_of: str) -> str:
    """market_yields.csv text with the USD SOFR index row set to value/as_of."""
    rows = list(csv.DictReader(io.StringIO(text)))
    fields = list(rows[0].keys()) if rows else []
    hits = [r for r in rows if r["currency"] == "USD" and r["rate_type"] == "index"
            and r["index_name"].strip().upper() == "SOFR"]
    if len(hits) != 1:
        raise ValueError(f"expected one USD SOFR index row, found {len(hits)}")
    hits[0].update({
        "refi_yield_pct": f"{value:.5f}",
        "as_of": as_of,
        "source": f"NY Fed 30-day Average SOFR, effective {as_of} ({PAGE}; "
                  "via markets.newyorkfed.org/api/rates/secured/sofrai)",
    })
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue()


def main() -> None:
    try:
        value, as_of = latest_sofr_average()
        text = set_sofr_row(OUTPUT.read_text(), value, as_of)
    except Exception as e:  # network, HTTP, parse, or missing row
        print(f"SOFR update failed ({e}); {OUTPUT.name} left unchanged.")
        sys.exit(1)
    tmp = OUTPUT.with_suffix(".tmp")
    tmp.write_text(text)
    tmp.replace(OUTPUT)
    print(f"30-day Average SOFR {as_of}: {value:.5f}% -> data/{OUTPUT.name}")


if __name__ == "__main__":
    main()
