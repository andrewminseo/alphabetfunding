"""
edgar_pull.py

Pulls Alphabet's filing index and XBRL debt facts from SEC EDGAR.

What it does:
  1. Lists every 10-K, 10-Q, 8-K, and 424B2 (bond prospectus supplement)
     filing and saves the index to data/filings_index.csv, with direct links.
  2. Pulls XBRL "company facts" for debt-related tags and saves them to
     data/xbrl_debt_facts.csv, so you can reconcile your tranche database
     against the totals Alphabet reports.
  3. Optionally downloads the primary document of selected filings to
     data/raw/ for reading the debt footnote and deal terms.

The SEC requires a descriptive User-Agent with contact info.
Set it with --user-agent or the SEC_USER_AGENT environment variable, e.g.
    "Sara Otsuki sotsuki@umich.edu"

Usage:
    python scripts/edgar_pull.py --user-agent "Your Name you@umich.edu"
    python scripts/edgar_pull.py --since 2025-01-01 --forms 424B2 --download
"""

import argparse
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

CIK = "0001652044"  # Alphabet Inc.
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"

SUBMISSIONS_URL = f"https://data.sec.gov/submissions/CIK{CIK}.json"
FACTS_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{CIK}.json"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"

DEFAULT_FORMS = ["10-K", "10-Q", "8-K", "424B2", "FWP"]

# XBRL tags worth reconciling against. Not every tag is used every period.
DEBT_TAGS = [
    "LongTermDebt",
    "LongTermDebtNoncurrent",
    "LongTermDebtCurrent",
    "DebtInstrumentCarryingAmount",
    "DebtInstrumentFaceAmount",
    "LongTermDebtFairValue",
    "InterestExpense",
    "InterestExpenseNonoperating",
    "ProceedsFromIssuanceOfLongTermDebt",
    "ProceedsFromIssuanceOfDebt",
    "RepaymentsOfDebt",
    "RepaymentsOfLongTermDebt",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",
]


def session(user_agent: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
    return s


def get_json(s: requests.Session, url: str) -> dict:
    r = s.get(url, timeout=30)
    r.raise_for_status()
    time.sleep(0.15)  # stay well under the SEC's 10 requests/second limit
    return r.json()


def filings_index(s: requests.Session, forms, since) -> pd.DataFrame:
    sub = get_json(s, SUBMISSIONS_URL)
    frames = [pd.DataFrame(sub["filings"]["recent"])]

    # Older filings live in additional JSON pages.
    for extra in sub["filings"].get("files", []):
        url = f"https://data.sec.gov/submissions/{extra['name']}"
        frames.append(pd.DataFrame(get_json(s, url)))

    df = pd.concat(frames, ignore_index=True)
    df = df[df["form"].isin(forms)].copy()
    df["filingDate"] = pd.to_datetime(df["filingDate"])
    if since:
        df = df[df["filingDate"] >= pd.Timestamp(since)]

    cik_int = str(int(CIK))
    df["url"] = df.apply(
        lambda r: ARCHIVE_BASE.format(
            cik=cik_int,
            acc=r["accessionNumber"].replace("-", ""),
            doc=r["primaryDocument"],
        ),
        axis=1,
    )
    cols = ["filingDate", "form", "accessionNumber", "reportDate",
            "primaryDocument", "primaryDocDescription", "url"]
    return df[cols].sort_values("filingDate", ascending=False).reset_index(drop=True)


def debt_facts(s: requests.Session) -> pd.DataFrame:
    facts = get_json(s, FACTS_URL)["facts"].get("us-gaap", {})
    rows = []
    for tag in DEBT_TAGS:
        if tag not in facts:
            continue
        for unit, entries in facts[tag]["units"].items():
            for e in entries:
                rows.append({
                    "tag": tag,
                    "unit": unit,
                    "value": e.get("val"),
                    "period_start": e.get("start"),
                    "period_end": e.get("end"),
                    "form": e.get("form"),
                    "fiscal_year": e.get("fy"),
                    "fiscal_period": e.get("fp"),
                    "filed": e.get("filed"),
                    "accession": e.get("accn"),
                })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["tag", "period_end", "filed"]).reset_index(drop=True)
    return df


def download(s: requests.Session, idx: pd.DataFrame) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for _, r in idx.iterrows():
        name = f"{r['filingDate']:%Y-%m-%d}_{r['form']}_{r['primaryDocument']}"
        path = RAW / name.replace("/", "_")
        if path.exists():
            continue
        resp = s.get(r["url"], timeout=60)
        resp.raise_for_status()
        path.write_bytes(resp.content)
        print(f"  saved {path.name}")
        time.sleep(0.15)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT"))
    p.add_argument("--forms", nargs="+", default=DEFAULT_FORMS)
    p.add_argument("--since", default="2020-01-01", help="YYYY-MM-DD")
    p.add_argument("--download", action="store_true",
                   help="download primary documents of the listed filings")
    p.add_argument("--skip-facts", action="store_true")
    args = p.parse_args()

    if not args.user_agent:
        sys.exit("Set --user-agent or SEC_USER_AGENT (name + email), per SEC policy.")

    DATA.mkdir(exist_ok=True)
    s = session(args.user_agent)

    idx = filings_index(s, args.forms, args.since)
    idx.to_csv(DATA / "filings_index.csv", index=False)
    print(f"Filings index: {len(idx)} filings -> data/filings_index.csv")
    print(idx["form"].value_counts().to_string())

    if not args.skip_facts:
        facts = debt_facts(s)
        facts.to_csv(DATA / "xbrl_debt_facts.csv", index=False)
        print(f"XBRL debt facts: {len(facts)} rows -> data/xbrl_debt_facts.csv")

    if args.download:
        print(f"Downloading {len(idx)} documents to data/raw/ ...")
        download(s, idx)


if __name__ == "__main__":
    main()
