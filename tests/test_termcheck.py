"""Offline tests for scripts/termcheck.py against the April 2025 USD golden deal."""

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import termcheck as tc  # noqa: E402

SETTLE = "2025-05-01"
GOLDEN = list(csv.DictReader(open(ROOT / "tests" / "golden" / "2025-04-28_usd.csv")))

# Synthetic term sheet containing every golden value, in filing formats.
TEXT = "\n".join(
    f"{g['maturity'][:4]} Notes: ${int(g['principal']):,} | {g['coupon']}% per annum | "
    f"{g['price']}% | Yield {g['yield']}% | T + {g['spread_bps']} bps | "
    f"yield {g['benchmark_yield']}% | May 15, {g['maturity'][:4]} | "
    f"{g['cusip'][:6]} {g['cusip'][6:]}"
    for g in GOLDEN
)


def tranche(g: dict) -> dict:
    return {
        "rate_type": "fixed",
        "principal": int(g["principal"]),
        "coupon_pct": float(g["coupon"]),
        "maturity_date": g["maturity"],
        "issue_price_pct": float(g["price"]),
        "issue_yield_pct": float(g["yield"]),
        "spread_bps": float(g["spread_bps"]),
        "benchmark_yield_pct": float(g["benchmark_yield"]),
        "cusip": g["cusip"],
    }


def check(t: dict, text: str = TEXT) -> tc.CheckResult:
    return tc.check_tranche(t, text, currency="USD", settlement_date=SETTLE, freq=2)


@pytest.mark.parametrize("cusip", [g["cusip"] for g in GOLDEN])
def test_golden_cusips_valid(cusip):
    assert tc.valid_cusip(cusip)


def test_bad_cusip_check_digit():
    assert not tc.valid_cusip("02079KAK4")


@pytest.mark.parametrize("g", GOLDEN, ids=lambda g: g["maturity"])
def test_yield_from_price_within_1bp(g):
    y = tc.yield_from_price(float(g["price"]), float(g["coupon"]), SETTLE, g["maturity"], freq=2)
    assert abs(y - float(g["yield"])) * 100 <= 1.0


@pytest.mark.parametrize("g", GOLDEN, ids=lambda g: g["maturity"])
def test_golden_tranches_pass(g):
    r = check(tranche(g))
    assert r.status == tc.PASS, r.issues


def test_swapped_spreads_fail():
    a, b = tranche(GOLDEN[0]), tranche(GOLDEN[1])
    a["spread_bps"], b["spread_bps"] = b["spread_bps"], a["spread_bps"]
    for t in (a, b):
        r = check(t)
        assert r.status == tc.FAIL
        assert any(i.field == "spread_bps" for i in r.issues)


def test_number_absent_from_text_fails_grounding():
    text = "2030 Notes: $750,000,000 | 4.000% | 99.417% | May 15, 2030"
    t = {"rate_type": "fixed", "principal": 750000000, "coupon_pct": 4.0,
         "maturity_date": "2030-05-15", "issue_price_pct": 99.417,
         "issue_yield_pct": 4.129}  # 4.129 is not in the text
    r = check(t, text)
    assert r.status == tc.FAIL
    assert any(i.field == "issue_yield_pct" and "not found" in i.message for i in r.issues)
    assert r.evidence["principal"] is not None
    assert r.evidence["issue_yield_pct"] is None


CUR_TRANCHE = {"rate_type": "fixed", "principal": 750000000, "coupon_pct": 4.0,
               "maturity_date": "2030-05-15"}


def currency_issues(text: str, currency: str) -> tuple[str, list]:
    r = tc.check_tranche(CUR_TRANCHE, text + " | 4.000% | May 15, 2030",
                         currency=currency, settlement_date=SETTLE, freq=2)
    return r.status, [i for i in r.issues if i.field == "currency"]


@pytest.mark.parametrize("text,currency", [
    ("2030 Notes: $750,000,000", "USD"),
    ("2030 Notes: €750,000,000", "EUR"),
    ("2030 Notes: C$750,000,000", "CAD"),
    ("2030 Notes: CHF 750,000,000", "CHF"),
])
def test_currency_symbol_matches(text, currency):
    status, issues = currency_issues(text, currency)
    assert issues == [], issues


def test_currency_symbol_mismatch_fails():
    status, issues = currency_issues("2030 Notes: €750,000,000", "USD")
    assert status == tc.FAIL
    assert issues and issues[0].level == tc.FAIL


def test_currency_symbol_missing_warns():
    status, issues = currency_issues("2030 Notes: 750,000,000", "USD")
    assert status == tc.WARN
    assert issues and issues[0].level == tc.WARN
