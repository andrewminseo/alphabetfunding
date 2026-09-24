"""
extract_terms.py

Turns SEC pricing term sheets (FWP filings) into candidate rows for
data/tranches.csv, using a local model through Ollama. The model only does data
entry: every tranche it returns is checked by termcheck.check_tranche before it
lands in data/tranches_pending.csv for review. Nothing here writes tranches.csv;
approved rows move over with promote.py.

Usage:
    python scripts/extract_terms.py --url <FWP url> [--url ...]
    python scripts/extract_terms.py --index            # skips accessions already audited
    python scripts/extract_terms.py --index --force    # reprocess everything
    python scripts/extract_terms.py --url <FWP url> --golden tests/golden/2025-04-28_usd.csv
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))
import termcheck  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
TRANCHES = DATA / "tranches.csv"
PENDING = DATA / "tranches_pending.csv"
AUDIT = DATA / "extraction_audit.jsonl"

OLLAMA_URL = "http://localhost:11434/api/chat"
FOOTER = "has filed a registration statement"

BASE_COLS = [
    "tranche_id", "issuer", "currency", "principal_local", "coupon_pct",
    "issue_price_pct", "issue_yield_pct", "benchmark", "issue_spread_bps",
    "issue_date", "maturity_date", "rate_type", "secured", "source_filing",
    "source_url", "notes", "floating_margin_bps",
]
EXTRA_COLS = [
    "cusip", "isin", "benchmark_yield_pct", "underwriting_discount_pct",
    "net_proceeds_local", "trade_date", "source_accession", "model", "status",
    "issues", "approved",
]
PENDING_COLS = BASE_COLS + EXTRA_COLS

BENCHMARK_PREFIX = {"treasury": "UST", "bund": "DBR", "gilt": "UKT"}


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

class Tranche(BaseModel):
    series_label: Optional[str] = Field(..., description='e.g. "2030 Notes"')
    rate_type: Optional[Literal["fixed", "floating"]] = Field(...)
    principal: Optional[float] = Field(..., description="full currency units, e.g. 750000000")
    coupon_pct: Optional[float] = Field(..., description="e.g. 4.000 for 4.000%")
    maturity_date: Optional[str] = Field(..., description="YYYY-MM-DD")
    issue_price_pct: Optional[float] = Field(..., description="public offering price, % of principal")
    underwriting_discount_pct: Optional[float] = Field(...)
    net_proceeds: Optional[float] = Field(..., description="full currency units")
    issue_yield_pct: Optional[float] = Field(..., description="yield to maturity at issue")
    spread_bps: Optional[float] = Field(..., description="issue spread to the benchmark, in bps")
    benchmark_description: Optional[str] = Field(..., description='e.g. "3.875% due April 30, 2030"')
    benchmark_yield_pct: Optional[float] = Field(...)
    floating_index: Optional[str] = Field(...)
    floating_margin_bps: Optional[float] = Field(...)
    cusip: Optional[str] = Field(...)
    isin: Optional[str] = Field(...)


class Deal(BaseModel):
    issuer: Optional[str] = Field(...)
    currency: Optional[str] = Field(..., description="ISO code, e.g. USD, EUR")
    trade_date: Optional[str] = Field(..., description="YYYY-MM-DD")
    settlement_date: Optional[str] = Field(..., description="YYYY-MM-DD")
    ranking: Optional[str] = Field(...)
    benchmark_type: Optional[str] = Field(..., description="e.g. Treasury, Bund, Gilt, Mid-Swaps")
    tranches: list[Tranche]


PROMPT = """You are a data-entry clerk. Copy bond terms from the pricing term sheet below into the JSON schema.

Rules:
- Only copy values printed in the term sheet. Never infer, compute, or guess. If a field is not in the term sheet, use null.
- One tranche per series of notes. Many term sheets list all series inside one table cell, one line per series, prefixed with the series name (e.g. "2030 Notes: ..."). Match each value to its series by that prefix, not by position.
- Percentages as plain numbers (4.000% -> 4.0). Basis points as plain numbers ("T + 32 bps" -> 32). Dates as YYYY-MM-DD. Principal and net proceeds in full currency units ($750,000,000 -> 750000000).
- spread_bps is the issue spread to the benchmark at pricing (e.g. "Spread to Benchmark Treasury"). Make-whole or redemption spreads ("Treasury Rate plus X basis points", "Bund Rate plus X basis points") are NOT the issue spread; never use them for spread_bps.
- benchmark_yield_pct is the benchmark security's yield at pricing, not its coupon.
- CUSIP is 9 characters with no spaces.
- Floating rate notes: the margin over the floating index (e.g. SOFR plus a percentage) goes in floating_margin_bps, converted to basis points (a margin of 0.25% is 25). A margin over SOFR is not a spread to a benchmark: for floating notes, spread_bps and coupon_pct are null.
"""


# --------------------------------------------------------------------------
# Fetch and text
# --------------------------------------------------------------------------

def accession_from_url(url: str) -> str:
    m = re.search(r"/data/\d+/(\d{18})/", url)
    return f"{m.group(1)[:10]}-{m.group(1)[10:12]}-{m.group(1)[12:]}" if m else ""


def fetch(url: str, user_agent: str) -> str:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"fwp_{accession_from_url(url)}_{url.rsplit('/', 1)[-1]}"
    if path.exists():
        return path.read_bytes().decode("utf-8", errors="replace")
    r = requests.get(url, headers={"User-Agent": user_agent,
                                   "Accept-Encoding": "gzip, deflate"}, timeout=60)
    r.raise_for_status()
    time.sleep(0.15)
    path.write_bytes(r.content)
    return r.content.decode("utf-8", errors="replace")


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for table in soup.find_all("table"):
        lines = []
        for tr in table.find_all("tr"):
            cells = [td.get_text("\n", strip=True) for td in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                lines.append(" | ".join(cells))
        table.replace_with("\n" + "\n".join(lines) + "\n")
    text = soup.get_text("\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    cut = text.find(FOOTER)
    if cut != -1:
        text = text[:text.rfind("\n", 0, cut) + 1 or cut]
    return text.strip()


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

EQUITY_TERMS = re.compile(r"Common Stock|Capital Stock|Depositary Shares", re.IGNORECASE)


def cover(text: str) -> str:
    """Title/cover and offering description: everything before the first "Issuer:"."""
    i = text.find("Issuer:")
    return text[:i] if i > 0 else text[:3000]


def non_debt_reason(text: str) -> str | None:
    c = cover(text)
    if not re.search(r"Notes\s+due", c, re.IGNORECASE):
        return 'no "Notes due" in cover'
    m = EQUITY_TERMS.search(c)
    if m:
        return f'cover mentions "{m.group(0)}"'
    return None


def yield_basis_from_text(text: str) -> str | None:
    """ "semi-annual" if the filing labels yields "(Semi-Annual / Annual)"."""
    if re.search(r"Yield\s+to\s+Maturity\s*\(\s*Semi-Annual\s*/\s*Annual\s*\)", text, re.IGNORECASE):
        return "semi-annual"
    return None


def extract(text: str, model: str) -> tuple[str, Deal]:
    r = requests.post(OLLAMA_URL, json={
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT},
            {"role": "user", "content": text},
        ],
        "format": Deal.model_json_schema(),
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 16384},
    }, timeout=600)
    r.raise_for_status()
    raw = r.json()["message"]["content"]
    return raw, Deal.model_validate_json(raw)


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def benchmark_prefix(deal: Deal, text: str) -> str | None:
    """UST/DBR/UKT from the model's benchmark_type, else from the filing's own
    "Benchmark Treasury/Bund/Gilt" label. None if neither is recognizable."""
    prefix = BENCHMARK_PREFIX.get((deal.benchmark_type or "").strip().lower())
    if prefix:
        return prefix
    labels = {m.lower() for m in re.findall(r"Benchmark\s+(Treasury|Bund|Gilt)", text, re.IGNORECASE)}
    return BENCHMARK_PREFIX[labels.pop()] if len(labels) == 1 else None


def fmt_benchmark(t: Tranche, prefix: str | None) -> str:
    desc = (t.benchmark_description or "").strip()
    if not desc:
        return ""
    m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*due\s+(.+)", desc, re.IGNORECASE)
    if prefix and m:
        try:
            due = pd.Timestamp(m.group(2).strip().rstrip(".")).date()
            return f"{prefix} {float(m.group(1)):.3f}% due {due.isoformat()}"
        except (ValueError, TypeError):
            pass
    return desc


def tranche_id(currency: str, settlement: str, maturity: str, floating: bool) -> str:
    parts = ["GOOGL", settlement[:7]]
    if currency != "USD":
        parts.append(currency)
    parts.append(maturity[:4])
    return "-".join(parts) + ("-FRN" if floating else "")


def num(x, fmt: str = "{:g}"):
    return "" if x is None else fmt.format(x)


def build_row(deal: Deal, t: Tranche, result: termcheck.CheckResult,
              url: str, model: str, prefix: str | None) -> dict:
    currency = (deal.currency or "").upper()
    floating = t.rate_type == "floating"
    ranking = deal.ranking or ""
    return {
        "tranche_id": tranche_id(currency, deal.settlement_date or "", t.maturity_date or "", floating)
        if deal.settlement_date and t.maturity_date else "",
        "issuer": deal.issuer or "",
        "currency": currency,
        "principal_local": num(t.principal, "{:.0f}"),
        "coupon_pct": num(t.coupon_pct, "{:.3f}"),
        "issue_price_pct": num(t.issue_price_pct, "{:.3f}"),
        "issue_yield_pct": num(t.issue_yield_pct, "{:.3f}"),
        "benchmark": fmt_benchmark(t, prefix),
        "issue_spread_bps": num(t.spread_bps),
        "issue_date": deal.settlement_date or "",
        "maturity_date": t.maturity_date or "",
        "rate_type": t.rate_type or "",
        "secured": "no" if "unsecured" in ranking.lower() else "",
        "source_filing": f"FWP {deal.trade_date}" if deal.trade_date else "FWP",
        "source_url": url,
        "notes": ranking,
        "floating_margin_bps": num(t.floating_margin_bps),
        "cusip": re.sub(r"\s", "", t.cusip or ""),
        "isin": re.sub(r"\s", "", t.isin or ""),
        "benchmark_yield_pct": num(t.benchmark_yield_pct, "{:.3f}"),
        "underwriting_discount_pct": num(t.underwriting_discount_pct, "{:.3f}"),
        "net_proceeds_local": num(t.net_proceeds, "{:.0f}"),
        "trade_date": deal.trade_date or "",
        "source_accession": accession_from_url(url),
        "model": model,
        "status": result.status,
        "issues": "; ".join(f"{i.level} {i.field}: {i.message}" for i in result.issues),
        "approved": "",
    }


def dedup_key(currency, maturity, coupon) -> tuple:
    try:
        c = round(float(coupon), 4)
    except (TypeError, ValueError):
        c = None
    return (str(currency).upper(), str(maturity)[:10], c)


def existing_keys(path: Path) -> set:
    if not path.exists():
        return set()
    df = pd.read_csv(path, dtype=str).fillna("")
    return {dedup_key(r.currency, r.maturity_date, r.coupon_pct) for r in df.itertuples()}


def processed_accessions(path: Path) -> set[str]:
    """Accessions that already have an entry in the audit log."""
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text().splitlines():
        if line.strip():
            done.add(json.loads(line).get("accession", ""))
    return done - {""}


def split_processed(urls: list[str], done: set[str]) -> tuple[list[str], list[str]]:
    """(to process, already processed) by accession."""
    todo, skipped = [], []
    for u in urls:
        (skipped if accession_from_url(u) in done else todo).append(u)
    return todo, skipped


def append_pending(rows: list[dict]) -> None:
    if not rows:
        return
    new = pd.DataFrame(rows, columns=PENDING_COLS)
    if PENDING.exists():
        old = pd.read_csv(PENDING, dtype=str).fillna("")
        new = pd.concat([old, new], ignore_index=True)
    new.to_csv(PENDING, index=False)


# --------------------------------------------------------------------------
# Golden
# --------------------------------------------------------------------------

GOLDEN_MAP = [  # golden column, row column, numeric?
    ("principal", "principal_local", True),
    ("coupon", "coupon_pct", True),
    ("price", "issue_price_pct", True),
    ("yield", "issue_yield_pct", True),
    ("spread_bps", "issue_spread_bps", True),
    ("benchmark", "benchmark", False),
    ("benchmark_yield", "benchmark_yield_pct", True),
    ("cusip", "cusip", False),
]


def compare_golden(rows: list[dict], golden_path: Path) -> list[str]:
    golden = pd.read_csv(golden_path, dtype=str).fillna("")
    by_mat = {r["maturity_date"]: r for r in rows}
    errors = []
    for g in golden.to_dict("records"):
        row = by_mat.get(g["maturity"])
        if row is None:
            errors.append(f"{g['maturity']}: tranche not extracted")
            continue
        for gcol, rcol, numeric in GOLDEN_MAP:
            want, got = g[gcol], row[rcol]
            if numeric:
                ok = got != "" and abs(float(want) - float(got)) < 1e-9
            else:
                ok = want.strip() == got.strip()
            if not ok:
                errors.append(f"{g['maturity']} {gcol}: expected {want!r}, got {got!r}")
    extra = set(by_mat) - set(golden["maturity"])
    for m in sorted(extra):
        errors.append(f"{m}: extracted but not in golden file")
    return errors


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def print_summary(row: dict, dup: bool, issues: list) -> None:
    tag = row["status"] + ("  DUPLICATE (already in tranches.csv)" if dup else "")
    print(f"  {row['tranche_id'] or '(no id)':<26} {tag}")
    print(f"    {row['currency']} {row['principal_local']}  cpn {row['coupon_pct']}  "
          f"px {row['issue_price_pct']}  yld {row['issue_yield_pct']}  "
          f"spr {row['issue_spread_bps']}  mat {row['maturity_date']}")
    print(f"    bmk {row['benchmark']} @ {row['benchmark_yield_pct']}  cusip {row['cusip']}"
          + (f"  margin {row['floating_margin_bps']} bp" if row["floating_margin_bps"] else ""))
    for i in issues:
        print(f"    - {i.level} {i.field}: {i.message}")


def process(url: str, args, pending_keys: set, db_keys: set,
            write_audit: bool = True) -> tuple[list[dict], list[dict]]:
    text = html_to_text(fetch(url, args.user_agent))
    if "pricing term sheet" not in text.lower():
        print(f"SKIP {url}: no 'Pricing Term Sheet'")
        return [], []
    reason = non_debt_reason(text)
    if reason:
        print(f"SKIP (non-debt) {url}: {reason}")
        return [], []
    print(f"\n{url}")
    raw, deal = extract(text, args.model)
    currency = (deal.currency or "").upper()
    freq = args.freq or (2 if currency == "USD" else 1)
    prefix = benchmark_prefix(deal, text)
    yield_basis = yield_basis_from_text(text)

    dumped = [t.model_dump() for t in deal.tranches]
    results = [termcheck.check_tranche(d, text, currency=currency, settlement_date=deal.settlement_date,
                                       freq=freq, yield_basis=yield_basis) for d in dumped]
    termcheck.check_deal(dumped, results)

    rows, audit_tranches, new_rows = [], [], []
    for t, result in zip(deal.tranches, results):
        row = build_row(deal, t, result, url, args.model, prefix)
        rows.append(row)
        key = dedup_key(currency, row["maturity_date"], row["coupon_pct"])
        dup = key in db_keys
        print_summary(row, dup, result.issues)
        audit_tranches.append({"series_label": t.series_label, "tranche_id": row["tranche_id"],
                               "status": result.status, "duplicate": dup,
                               "issues": [asdict(i) for i in result.issues],
                               "evidence": result.evidence})
        if not dup and key not in pending_keys:
            new_rows.append(row)
            pending_keys.add(key)

    if not write_audit:
        return rows, new_rows
    with AUDIT.open("a") as f:
        f.write(json.dumps({"url": url, "accession": accession_from_url(url),
                            "model": args.model, "run_at": date.today().isoformat(),
                            "yield_basis": yield_basis or f"coupon frequency ({freq}/yr)",
                            "raw_model_output": raw, "tranches": audit_tranches}) + "\n")
    return rows, new_rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", action="append", default=[])
    p.add_argument("--index", action="store_true", help="process FWPs in data/filings_index.csv")
    p.add_argument("--model", default="qwen2.5:7b")
    p.add_argument("--freq", type=int, help="coupon frequency override (default 2 USD, 1 otherwise)")
    p.add_argument("--golden", type=Path, help="golden CSV to compare against; exits 1 on mismatch. "
                   "Writes nothing to the pending or audit files.")
    p.add_argument("--force", action="store_true",
                   help="with --index, reprocess FWPs already in the audit log")
    p.add_argument("--user-agent", default=os.environ.get("SEC_USER_AGENT"))
    args = p.parse_args()

    if not args.user_agent:
        sys.exit("Set --user-agent or SEC_USER_AGENT (name + email), per SEC policy.")
    urls = list(args.url)
    if args.index:
        idx = pd.read_csv(DATA / "filings_index.csv")
        index_urls = idx.loc[idx["form"] == "FWP", "url"].tolist()
        if not args.force:
            index_urls, skipped = split_processed(index_urls, processed_accessions(AUDIT))
            print(f"Skipped {len(skipped)} FWPs already processed (see {AUDIT.name}; --force to redo)")
        urls += index_urls
    if not urls:
        if args.index:
            print("No new FWPs to process.")
            return
        sys.exit("Nothing to do: pass --url or --index.")
    write = args.golden is None

    db_keys = existing_keys(TRANCHES)
    pending_keys = existing_keys(PENDING)
    all_rows, all_new = [], []
    for url in urls:
        rows, new = process(url, args, pending_keys, db_keys, write_audit=write)
        all_rows += rows
        all_new += new

    if write:
        append_pending(all_new)
        print(f"\n{len(all_rows)} tranches extracted, {len(all_new)} new -> {PENDING.relative_to(ROOT)}")
        print(f"Audit -> {AUDIT.relative_to(ROOT)}")
    else:
        print(f"\n{len(all_rows)} tranches extracted (golden run: pending and audit files not written)")

    if args.golden:
        errors = compare_golden(all_rows, args.golden)
        if errors:
            print(f"\nGOLDEN FAIL ({len(errors)} mismatches):")
            for e in errors:
                print(f"  {e}")
            sys.exit(1)
        print(f"\nGOLDEN PASS: {args.golden}")


if __name__ == "__main__":
    main()
