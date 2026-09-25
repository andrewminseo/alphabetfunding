"""Offline tests for the deterministic parts of scripts/extract_terms.py."""

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import extract_terms as et  # noqa: E402
import termcheck as tc  # noqa: E402

DEBT_COVER = ("Pricing Term Sheet | Filed pursuant to Rule 433\nAlphabet Inc.\n"
              "4.000% Notes due 2030\n4.500% Notes due 2035\nPricing Term Sheet\nIssuer: | Alphabet Inc.")
EQUITY_COVER = ("Pricing Term Sheet | Free Writing Prospectus\nAlphabet Inc.\nConcurrent Offerings of\n"
                "25,459,689 Shares of Class A Common Stock\n167,500,000 Series A Depositary Shares\n"
                "Each Representing a 1/20th Interest in a Share of 6.25% Series A Mandatory "
                "Convertible Preferred Stock\nIssuer: | Alphabet Inc.")
EQUITY_RAW = ROOT / "data" / "raw" / "fwp_0001193125-26-254474_d152589dfwp.htm"


def test_debt_cover_is_not_skipped():
    assert et.non_debt_reason(DEBT_COVER) is None


def test_equity_cover_is_skipped():
    assert et.non_debt_reason(EQUITY_COVER) is not None


def test_notes_with_equity_terms_is_skipped():
    assert et.non_debt_reason("0.50% Notes due 2030 and Class C Capital Stock\nIssuer: | X") is not None


@pytest.mark.skipif(not EQUITY_RAW.exists(), reason="cached June 2026 equity FWP not present")
def test_june_2026_equity_filing_is_skipped():
    text = et.html_to_text(EQUITY_RAW.read_bytes().decode("utf-8", "replace"))
    assert et.non_debt_reason(text) is not None


def test_yield_basis_from_label():
    assert et.yield_basis_from_text("Yield to Maturity (Semi-Annual / Annual): | 4.100% / 4.142%") == "semi-annual"
    assert et.yield_basis_from_text("Yield to Maturity: | 4.129%") is None
    assert et.yield_basis_from_text("Currency: GBP. Yield to Maturity: | 4.1%") is None  # label, not currency


def test_cusip_and_isin_spaces_removed():
    deal = et.Deal(issuer="Alphabet Inc.", currency="USD", trade_date="2026-08-06",
                   settlement_date="2026-08-10", ranking="Senior unsecured",
                   benchmark_type="Treasury", tranches=[])
    t = et.Tranche(**{f: None for f in et.Tranche.model_fields} | {
        "rate_type": "fixed", "maturity_date": "2028-08-10", "cusip": "02079K CM7",
        "isin": "US02079K CM76"})
    row = et.build_row(deal, t, tc.CheckResult(), "https://x/1652044/000119312526338750/d.htm", "m", "UST")
    assert row["cusip"] == "02079KCM7"
    assert row["isin"] == "US02079KCM76"


# --- Incremental --index ---------------------------------------------------

URL_A = "https://www.sec.gov/Archives/edgar/data/1652044/000119312525100802/d806252dfwp.htm"
URL_B = "https://www.sec.gov/Archives/edgar/data/1652044/000119312526338750/d159970dfwp.htm"


def test_processed_accessions_read_from_audit(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"accession": "0001193125-25-100802", "tranches": []}\n\n')
    assert et.processed_accessions(audit) == {"0001193125-25-100802"}
    assert et.processed_accessions(tmp_path / "missing.jsonl") == set()


def test_already_processed_fwps_are_skipped(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"accession": "0001193125-25-100802"}\n')
    todo, skipped = et.split_processed([URL_A, URL_B], et.processed_accessions(audit))
    assert todo == [URL_B]
    assert skipped == [URL_A]


def test_nothing_skipped_with_empty_audit(tmp_path):
    todo, skipped = et.split_processed([URL_A, URL_B], et.processed_accessions(tmp_path / "none.jsonl"))
    assert todo == [URL_A, URL_B] and skipped == []


def test_build_row_carries_floating_margin():
    deal = et.Deal(issuer="Alphabet Inc.", currency="USD", trade_date="2025-11-03",
                   settlement_date="2025-11-06", ranking="Senior unsecured",
                   benchmark_type="Treasury", tranches=[])
    t = et.Tranche(**{f: None for f in et.Tranche.model_fields} | {
        "rate_type": "floating", "maturity_date": "2028-11-15", "floating_margin_bps": 52})
    row = et.build_row(deal, t, tc.CheckResult(), "https://x/1652044/000119312525263045/d.htm", "m", "UST")
    assert row["floating_margin_bps"] == "52"
    assert row["tranche_id"].endswith("-FRN")


def test_console_summary_keeps_issue_messages_whole(capsys):
    row = {c: "" for c in et.PENDING_COLS} | {"tranche_id": "GOOGL-2026-08-2028", "status": "WARN"}
    issues = [tc.Issue("WARN", "spread_bps", "spread missing; yield minus benchmark = 33.0 bp")]
    et.print_summary(row, False, issues)
    out = capsys.readouterr().out
    assert "    - WARN spread_bps: spread missing; yield minus benchmark = 33.0 bp\n" in out


# --- Pricing Term Sheet phrase ---------------------------------------------

def test_pricing_term_sheet_split_across_lines():
    # August 2020 original FWP: <B>Pricing Term\nSheet </B>
    assert et.has_pricing_term_sheet("2.250% Notes due 2060\nPricing Term\nSheet\nIssuer: |")
    assert not et.has_pricing_term_sheet("Issuer Free Writing Prospectus dated June 1, 2026")


# --- Superseded filings (same CUSIPs, filed later) -------------------------

def test_cusips_in_text_validates_and_needs_a_letter():
    text = "CUSIP/ISIN: | 2030 Notes: 02079K AK3 / US02079KAK34 2035 Notes: 02079KAL1 | 123456782 | 02079KAK4"
    assert et.cusips_in_text(text) == {"02079KAK3", "02079KAL1"}  # bad check digit and all-digit token dropped


def test_later_filing_with_same_cusips_supersedes():
    a, b = "https://x/1652044/000119312520208301/d.htm", "https://x/1652044/000119312520208486/d.htm"
    c = "https://x/1652044/000119312525100802/d.htm"
    same = frozenset({"02079KAD9", "02079KAE7"})
    sup = et.superseded([(b, "2020-08-04", same), (a, "2020-08-03", same),
                         (c, "2025-04-28", frozenset({"02079KAK3"}))])
    assert sup == {a: b}


def test_no_cusips_never_grouped_and_subsets_not_merged():
    a, b = "https://x/1/000000000000000001/a.htm", "https://x/1/000000000000000002/b.htm"
    assert et.superseded([(a, "2020-01-01", frozenset()), (b, "2020-01-02", frozenset())]) == {}
    assert et.superseded([(a, "2020-01-01", frozenset({"02079KAD9"})),
                          (b, "2020-01-02", frozenset({"02079KAD9", "02079KAE7"}))]) == {}


# --- German benchmark prefixes ---------------------------------------------

@pytest.mark.parametrize("desc,expected", [
    ("OBL 2.100% due April 12, 2029", "OBL 2.100% due 2029-04-12"),
    ("DBR 2.300% due February 15, 2033", "DBR 2.300% due 2033-02-15"),
    ("BKO 1.900% due September 16, 2027", "BKO 1.900% due 2027-09-16"),
    ("2.500% due July 4, 2044", "DBR 2.500% due 2044-07-04"),  # no printed prefix: Bund default
])
def test_german_benchmark_prefix(desc, expected):
    t = et.Tranche(**{f: None for f in et.Tranche.model_fields} | {"benchmark_description": desc})
    assert et.fmt_benchmark(t, "DBR") == expected


# --- --recheck -------------------------------------------------------------

RECHECK_HTML = """<p>Pricing Term Sheet</p><table>
<tr><td>Aggregate Principal Amount:</td><td>2030 Notes: $750,000,000</td></tr>
<tr><td>Maturity Date:</td><td>2030 Notes: May 15, 2030</td></tr>
<tr><td>Coupon (Interest Rate):</td><td>2030 Notes: 4.000% per annum</td></tr>
<tr><td>Public Offering Price:</td><td>2030 Notes: 99.417%</td></tr>
<tr><td>Yield to Maturity:</td><td>2030 Notes: 4.129%</td></tr>
<tr><td>Spread to Benchmark Treasury:</td><td>2030 Notes: T + 32 bps</td></tr>
<tr><td>Benchmark Treasury Price and Yield:</td><td>2030 Notes: 100-09+ / 3.809%</td></tr>
<tr><td>Interest Payment Dates:</td><td>May 15 and November 15 of each year</td></tr>
<tr><td>CUSIP/ISIN:</td><td>2030 Notes: 02079K AK3</td></tr></table>"""
ACC = "0001193125-25-100802"


def recheck_env(tmp_path, monkeypatch, spread, status, approved):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / f"fwp_{ACC}_d.htm").write_text(RECHECK_HTML)
    monkeypatch.setattr(et, "RAW", raw)
    monkeypatch.setattr(et, "extract", lambda *a, **k: pytest.fail("recheck must not call the model"))
    row = {c: "" for c in et.PENDING_COLS} | {
        "tranche_id": "GOOGL-2025-05-2030", "currency": "USD", "principal_local": "750000000",
        "coupon_pct": "4.000", "issue_price_pct": "99.417", "issue_yield_pct": "4.129",
        "issue_spread_bps": spread, "issue_date": "2025-05-01", "maturity_date": "2030-05-15",
        "rate_type": "fixed", "cusip": "02079KAK3", "benchmark_yield_pct": "3.809",
        "source_accession": ACC, "status": status, "issues": "old issue", "approved": approved}
    pending = tmp_path / "pending.csv"
    pd.DataFrame([row], columns=et.PENDING_COLS).to_csv(pending, index=False)
    audit = tmp_path / "audit.jsonl"
    audit.write_text(json.dumps({"accession": ACC, "raw_model_output": json.dumps(
        {"tranches": [{"series_label": "2030 Notes", "cusip": "02079KAK3"}]})}) + "\n")
    return pending, audit


def test_recheck_updates_status_and_keeps_approval(tmp_path, monkeypatch):
    # Row was FAIL with a wrong spread; the reviewer fixed the spread by hand.
    pending, audit = recheck_env(tmp_path, monkeypatch, "32", "FAIL", "yes")
    changes = et.recheck(pending, audit)
    out = pd.read_csv(pending, dtype=str).fillna("").iloc[0]
    assert (out["status"], out["issues"], out["approved"]) == ("PASS", "", "yes")
    assert changes == [("GOOGL-2025-05-2030", "FAIL", "PASS")]


def test_recheck_flags_bad_edit(tmp_path, monkeypatch):
    pending, audit = recheck_env(tmp_path, monkeypatch, "47", "PASS", "")
    et.recheck(pending, audit)
    out = pd.read_csv(pending, dtype=str).fillna("").iloc[0]
    assert out["status"] == "FAIL" and "spread_bps" in out["issues"]


def test_recheck_marks_exempt_rows_and_checks_others(tmp_path, monkeypatch):
    pending, audit = recheck_env(tmp_path, monkeypatch, "47", "PASS", "yes")   # bad spread -> FAIL
    df = pd.read_csv(pending, dtype=str).fillna("")
    extra = df.iloc[0].copy()
    extra["tranche_id"] = "GOOGL-2026-02-CHF-2036-SERIES"
    pd.concat([df, extra.to_frame().T]).to_csv(pending, index=False)
    ex = tmp_path / "ex.csv"
    ex.write_text("tranche_id,reason,added\nGOOGL-2026-02-CHF-2036-SERIES,series-level,2026-09-25\n")
    monkeypatch.setattr(et, "EXEMPTIONS", ex)
    et.recheck(pending, audit)
    out = pd.read_csv(pending, dtype=str).fillna("").set_index("tranche_id")
    assert out.loc["GOOGL-2026-02-CHF-2036-SERIES", "status"] == "EXEMPT"
    assert out.loc["GOOGL-2026-02-CHF-2036-SERIES", "issues"] == "exempt: series-level"
    assert out.loc["GOOGL-2025-05-2030", "status"] == "FAIL"       # checks still run on the rest
