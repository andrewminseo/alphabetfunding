"""Offline tests for the deterministic parts of scripts/extract_terms.py."""

import sys
from pathlib import Path

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
