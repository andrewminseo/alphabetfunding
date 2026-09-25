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

def section(label: str, fmt) -> str:
    """One term-sheet row: "Label: | 2030 Notes: v 2035 Notes: v ..."."""
    return f"{label}: | " + "\n".join(f"{g['maturity'][:4]} Notes: {fmt(g)}" for g in GOLDEN)


# Synthetic term sheet containing every golden value, in the filing's layout.
TEXT = "\n".join([
    section("Aggregate Principal Amount", lambda g: f"${int(g['principal']):,}"),
    section("Maturity Date", lambda g: f"May 15, {g['maturity'][:4]}"),
    section("Coupon (Interest Rate)", lambda g: f"{g['coupon']}% per annum"),
    section("Public Offering Price", lambda g: f"{g['price']}%"),
    section("Yield to Maturity", lambda g: f"{g['yield']}%"),
    section("Spread to Benchmark Treasury", lambda g: f"T + {g['spread_bps']} bps"),
    section("Benchmark Treasury Price and Yield", lambda g: f"99-01 / {g['benchmark_yield']}%"),
    section("CUSIP/ISIN", lambda g: f"{g['cusip'][:6]} {g['cusip'][6:]}"),
    "Trade Date: | April 28, 2025",
])


def tranche(g: dict) -> dict:
    return {
        "series_label": f"{g['maturity'][:4]} Notes",
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


# --- Floating notes -------------------------------------------------------

FRN_TEXT = ("Aggregate Principal Amount: | 2028 Floating Rate Notes: $750,000,000\n"
            "Maturity Date: | 2028 Floating Rate Notes: August 10, 2028\n"
            "Coupon (Interest Rate): | 2028 Floating Rate Notes: Compounded SOFR, "
            "reset quarterly, plus 0.52% per annum\n"
            "Public Offering Price: | 2028 Floating Rate Notes: 100.000%")
FRN = {"series_label": "2028 Floating Rate Notes", "rate_type": "floating",
       "principal": 750000000, "maturity_date": "2028-08-10", "issue_price_pct": 100.0,
       "floating_index": "SOFR", "floating_margin_bps": 52}


def check_frn(t, text=FRN_TEXT):
    return tc.check_tranche(t, text, currency="USD", settlement_date="2026-08-10", freq=4)


def test_frn_margin_written_as_percent_grounds():
    r = check_frn(FRN)
    assert r.status == tc.PASS, r.issues
    assert "0.52%" in r.evidence["floating_margin_bps"]


def test_frn_margin_wrong_units_fails():  # "plus 0.44%" extracted as 440 bp
    r = check_frn({**FRN, "floating_margin_bps": 520})
    assert any(i.field == "floating_margin_bps" and i.level == tc.FAIL for i in r.issues)


def test_frn_coupon_must_be_null():
    r = check_frn({**FRN, "coupon_pct": 0.52})
    assert any(i.field == "coupon_pct" and i.level == tc.FAIL for i in r.issues)


def test_frn_spread_must_be_null():
    r = check_frn({**FRN, "spread_bps": 52})
    assert any(i.field == "spread_bps" and i.level == tc.FAIL for i in r.issues)


# --- Spreads across tranches (August 2026 pattern) -------------------------

AUG_TEXT = ("2028 Notes: $1,250,000,000 | 4.500% | 99.858% | 4.575% | T + 33 bps | "
            "4.245% | August 10, 2028 | 2028 Floating Rate Notes: $750,000,000 | "
            "SOFR plus 0.44% per annum | 100.000%")
AUG_FIXED = {"series_label": "2028 Notes", "rate_type": "fixed", "principal": 1250000000,
             "coupon_pct": 4.5, "maturity_date": "2028-08-10", "issue_price_pct": 99.858,
             "issue_yield_pct": 4.575, "benchmark_yield_pct": 4.245, "spread_bps": None}
AUG_FRN = {"series_label": "2028 Floating Rate Notes", "rate_type": "floating",
           "principal": 750000000, "maturity_date": "2028-08-10", "issue_price_pct": 100.0,
           "floating_index": "SOFR", "floating_margin_bps": 44, "spread_bps": 33}


def test_fixed_missing_spread_warns_with_implied():
    r = tc.check_tranche(AUG_FIXED, AUG_TEXT, currency="USD", settlement_date="2026-08-10", freq=2)
    msgs = [i.message for i in r.issues if i.field == "spread_bps"]
    assert r.status == tc.WARN
    assert msgs == ["spread missing; yield minus benchmark = 33.0 bp"]


def test_spread_copied_from_other_tranche_fails():
    ts = [AUG_FIXED, AUG_FRN]
    rs = [tc.check_tranche(t, AUG_TEXT, currency="USD", settlement_date="2026-08-10", freq=2) for t in ts]
    tc.check_deal(ts, rs)
    assert rs[1].status == tc.FAIL
    assert any("matches 2028 Notes's yield minus benchmark" in i.message for i in rs[1].issues)


def test_shared_spread_both_consistent_is_fine():
    a = tranche(GOLDEN[2])   # 2055: 5.314 - 4.694 = 62
    b = {**tranche(GOLDEN[3]), "spread_bps": 62.0, "issue_yield_pct": 5.314}  # consistent on its own
    rs = [tc.CheckResult(), tc.CheckResult()]
    tc.check_deal([a, b], rs)
    assert all(r.status == tc.PASS for r in rs)


# --- Yield basis (GBP, February 2026 pattern) -----------------------------

GBP_TEXT = ("Aggregate Principal Amount: | 2032 Notes: £1,250,000,000\n"
            "Maturity Date: | 2032 Notes: November 13, 2032\n"
            "Coupon (Interest Rate): | 2032 Notes: 4.625% per annum\n"
            "Public Offering Price: | 2032 Notes: 99.791%\n"
            "Yield to Maturity (Semi-Annual / Annual): | 2032 Notes: 4.612% / 4.665%\n"
            "Spread to Benchmark Gilt: | 2032 Notes: + 55 bps\n"
            "Benchmark Gilt Price and Yield: | 2032 Notes: 101.10 / 4.062%")
GBP = {"series_label": "2032 Notes", "rate_type": "fixed", "principal": 1250000000, "coupon_pct": 4.625,
       "maturity_date": "2032-11-13", "issue_price_pct": 99.791, "issue_yield_pct": 4.612,
       "spread_bps": 55, "benchmark_yield_pct": 4.062}


def check_gbp(**kw):
    return tc.check_tranche(GBP, GBP_TEXT, currency="GBP", settlement_date="2026-02-13", freq=1, **kw)


def test_semi_annual_yield_checked_against_converted_annual():
    r = check_gbp(yield_basis="semi-annual")
    assert r.status == tc.PASS, r.issues
    assert abs(float(r.evidence["yield_from_price_coupon_basis"]) - 4.665) < 0.01


def test_semi_annual_yield_fails_without_basis():
    assert check_gbp().status == tc.FAIL



# --- Series-tied grounding (August 2026 2046 maturity) ----------------------

AUG_SERIES_TEXT = (
    "Aggregate Principal Amount: | 2036 Notes: $4,500,000,000 2046 Notes: $3,000,000,000\n"
    "Maturity Date: | 2036 Notes: August 15, 2036 2046 Notes: August 15,\n2046\n"
    "Coupon (Interest Rate): | 2036 Notes: 5.450% per annum 2046 Notes: 6.250% per annum\n"
    "Public Offering Price: | 2036 Notes: 99.466% 2046 Notes: 99.626%\n"
    "Benchmark Treasury: | 2036 Notes: 4.375% due May 15, 2036 2046 Notes: 5.000% due May 15, 2046\n"
)
AUG_2046 = {"series_label": "2046 Notes", "rate_type": "fixed", "principal": 3000000000,
            "coupon_pct": 6.25, "maturity_date": "2046-08-15", "issue_price_pct": 99.626}


def check_aug(t, text=AUG_SERIES_TEXT):
    return tc.check_tranche(t, text, currency="USD", settlement_date="2026-08-10", freq=2)


def test_series_tied_values_pass():
    r = check_aug(AUG_2046)
    assert not [i for i in r.issues if i.field in ("maturity_date", "principal", "coupon_pct")], r.issues


def test_benchmark_date_as_maturity_fails():
    # The model gave the 2046 Notes their benchmark Treasury's maturity. The date
    # is in the filing (Benchmark Treasury row), so plain grounding passes it.
    r = check_aug({**AUG_2046, "maturity_date": "2046-05-15"})
    assert r.evidence["maturity_date"] is not None           # plain grounding found it
    assert r.status == tc.FAIL
    assert any(i.field == "maturity_date" and i.level == tc.FAIL for i in r.issues)


def test_other_series_principal_fails():
    r = check_aug({**AUG_2046, "principal": 4500000000})
    assert any(i.field == "principal" and i.level == tc.FAIL for i in r.issues)


def test_missing_series_label_warns():
    r = check_aug({**AUG_2046, "series_label": "2099 Notes"})
    assert r.status == tc.WARN
    assert any(i.field == "maturity_date" and "not found" in i.message for i in r.issues)


def test_missing_section_warns():
    r = check_aug(AUG_2046, AUG_SERIES_TEXT.replace("Maturity Date:", "Final Date:"))
    assert any(i.field == "maturity_date" and i.level == tc.WARN for i in r.issues)


def test_unlabeled_section_keyed_by_heading():
    # November 2025 layout: the FRN has its own block under a "Notes due" heading.
    text = ("Floating Rate Notes due 2028\nAggregate Principal Amount: | $500,000,000\n"
            "Maturity Date: | November 15, 2028\nCoupon (Interest Rate): | Compounded SOFR plus 0.52%")
    frn = {"series_label": "Floating Rate Notes due 2028", "rate_type": "floating",
           "principal": 500000000, "maturity_date": "2028-11-15", "floating_index": "SOFR"}
    r = tc.check_tranche(frn, text, currency="USD", settlement_date="2025-11-06", freq=4)
    assert not [i for i in r.issues if i.field in ("maturity_date", "principal")], r.issues


def test_no_yield_solves_price():
    assert tc.yield_from_price(0.99901, 0.45, "2020-08-05", "2025-08-15") is None
    r = tc.check_tranche({"rate_type": "fixed", "principal": 1, "coupon_pct": 0.45,
                          "maturity_date": "2025-08-15", "issue_price_pct": 0.99901,
                          "issue_yield_pct": 0.47}, "", currency="USD",
                         settlement_date="2020-08-05", freq=2)
    msgs = [i.message for i in r.issues if i.field == "issue_yield_pct"]
    assert any("no yield solves this price" in m for m in msgs)
    assert not any("50.000" in m for m in msgs)


@pytest.mark.parametrize("written", ["August 15th, 2026", "August 15 th , 2026", "15th August 2026"])
def test_dates_with_ordinal_suffix(written):
    # August 2016 FWP: "Maturity Date: | August 15 th , 2026" (superscript "th")
    text = (f"1.998% Notes due 2026\nAggregate Principal Amount: | $2,000,000,000\n"
            f"Maturity Date: | {written}\nCoupon (Interest Rate): | 1.998% per annum")
    t = {"series_label": "1.998% Notes due 2026", "rate_type": "fixed", "principal": 2000000000,
         "coupon_pct": 1.998, "maturity_date": "2026-08-15"}
    r = tc.check_tranche(t, text, currency="USD", settlement_date="2016-08-09", freq=2)
    assert not [i for i in r.issues if i.field == "maturity_date"], r.issues


def test_ordinal_does_not_hide_wrong_date():
    text = "Notes due 2026\nMaturity Date: | August 15 th , 2026\nAggregate Principal Amount: | $1"
    t = {"series_label": "Notes due 2026", "rate_type": "fixed", "principal": 1,
         "coupon_pct": None, "maturity_date": "2026-05-15"}
    r = tc.check_tranche(t, text, currency="USD", settlement_date="2016-08-09", freq=2)
    assert any(i.field == "maturity_date" and i.level == tc.FAIL for i in r.issues)
