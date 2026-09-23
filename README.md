# Alphabet Treasury Lab

Tracking how Alphabet funds its AI infrastructure buildout: what it borrows, in which currencies, at what cost, and when it comes due.

## Setup

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name you@umich.edu"   # SEC requires name + email
```

## Workflow

**1. Pull the filings index and XBRL debt totals**

```bash
python scripts/edgar_pull.py --since 2020-01-01
```

This writes `data/filings_index.csv` (every 10-K, 10-Q, 8-K, and 424B2 with links) and `data/xbrl_debt_facts.csv` (reported debt totals, maturities by year, interest expense). To download the documents themselves:

```bash
python scripts/edgar_pull.py --since 2025-01-01 --forms 424B2 8-K --download
```

**2. Build the tranche database by hand**

Fill `data/tranches.csv`, one row per note. Where to find terms:

| Source | What it gives you |
|---|---|
| 424B2 prospectus supplements | Exact coupon, size, maturity, and pricing for USD deals |
| 8-K filings around issuance dates | Underwriting agreements and note forms, often with foreign-currency deal terms |
| 10-K / 10-Q debt footnote | Outstanding notes by currency, coupon ranges, total carrying value |
| Bloomberg (Ross terminals) | Tranche-level data for non-USD deals, yields and spreads at issue |

Fill in `source_filing` and `source_url` for every row. If a number can't be traced to a document, it doesn't go in.

**3. Reconcile.** Your tranche total (converted to USD) should land close to reported `LongTermDebt` in `xbrl_debt_facts.csv`. Differences come from FX, discounts and issuance costs, and any notes you've missed. Chase down anything large.

**4. Update market inputs.** Replace the PLACEHOLDER values in `data/fx_rates.csv` and `data/market_yields.csv`. `refi_yield_pct` is your estimate of where Alphabet could issue today in that currency (benchmark yield plus spread).

**5. Run the analytics**

```bash
python scripts/treasury.py --as-of 2026-09-18
python scripts/treasury.py --sample          # test run on fake data
```

This prints the summary and writes `output/maturity_ladder.png`, `maturity_ladder.csv`, and `refi_sensitivity.csv`.

## LLM extraction

Pricing term sheets (FWP filings) can be turned into candidate tranche rows by a local model through [Ollama](https://ollama.com) (`ollama pull qwen2.5:7b`). The model only does data entry. Every tranche is checked by `scripts/termcheck.py`: every number must appear in the filing, CUSIP/ISIN check digits must be valid, the yield recomputed from the price must match, spread must equal yield minus benchmark yield, and net proceeds must add up. Results land in a pending file for you to review; nothing reaches `tranches.csv` until you approve it.

```bash
python scripts/edgar_pull.py --since 2025-01-01          # index now includes FWP
python scripts/extract_terms.py --index                  # or --url <FWP url>
# review data/tranches_pending.csv (status, issues, and evidence in
# data/extraction_audit.jsonl); set approved = yes on rows you've checked
python scripts/promote.py                                # approved, non-FAIL rows -> tranches.csv
python scripts/treasury.py
```

Regression check against hand-verified values: `python scripts/extract_terms.py --url https://www.sec.gov/Archives/edgar/data/1652044/000119312525100802/d806252dfwp.htm --golden tests/golden/2025-04-28_usd.csv`. Offline checks: `pytest`.

## Data files

| File | Contents |
|---|---|
| `data/tranches.csv` | Your real database. Starts empty. |
| `data/sample_tranches.csv` | Illustrative fake data for testing. Never cite it. |
| `data/fx_rates.csv` | USD per unit of each currency |
| `data/market_yields.csv` | Assumed refinancing yield by currency |

## Model limitations

- Refinancing sensitivity assumes each tranche is rolled over 1:1 in the same currency, with FX held constant and no swaps.
- USD conversion uses a single FX snapshot, not issue-date rates.
- Carrying value differs from face value; this model uses face value.

## Roadmap

- [ ] Real tranche database, reconciled to reported debt
- [ ] Research note: non-USD issuance and swapped-to-USD cost
- [ ] Issue-date FX and USD-equivalent at issuance
- [ ] Spread at issuance vs. benchmark (FRED for USD)
- [ ] Peers: Amazon, Meta, Microsoft
- [ ] Streamlit dashboard
- [ ] Funding map: leases, guarantees, third-party structures (sourced, estimates labeled)
