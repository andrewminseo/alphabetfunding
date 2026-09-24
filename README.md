# Alphabet Treasury Lab

Tracking how Alphabet funds its AI infrastructure buildout: what it borrows, in which currencies, at what cost, and when it comes due.

## Setup

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name you@umich.edu"   # SEC requires name + email
```

## Workflow

**1. Run the pipeline**

```bash
ollama serve                          # local model for extraction (ollama pull qwen2.5:14b)
python scripts/run_pipeline.py
```

It runs these steps in order and stops at the first error:

| Step | What it does |
|---|---|
| `edgar_pull.py` | Refreshes `data/filings_index.csv` (10-K, 10-Q, 8-K, 424B2, FWP) and `data/xbrl_debt_facts.csv` |
| `extract_terms.py --index` | Extracts tranches from new FWP pricing term sheets into `data/tranches_pending.csv`; FWPs already in `data/extraction_audit.jsonl` are skipped (`--force` to redo) |
| `update_fx.py` | Refreshes `data/fx_rates.csv` |
| `update_yields.py` | Writes the latest U.S. Treasury par yield curve to `data/treasury_curve.csv` |
| `treasury.py` | Summary, maturity ladder, refinancing sensitivity -> `output/` |
| reconciliation | Live tranche face value in USD vs. latest reported `LongTermDebt` |

Extraction uses `qwen2.5:14b` by default, which needs about 10 GB of free memory. On smaller machines, run `python scripts/extract_terms.py --index --model qwen2.5:7b` instead: it works, but makes more mistakes, so expect more WARN and FAIL rows to review.

It ends with a summary: new filings, new pending rows by status, rows needing your review, and the reconciliation gap. It never promotes anything.

**2. Review pending rows.** Open `data/tranches_pending.csv`. Each row has a `status` (PASS / WARN / FAIL) and `issues`; the evidence snippets and raw model output are in `data/extraction_audit.jsonl`. Check each row against the filing and set `approved` to `yes` on the ones you've verified.

The model only does data entry. Every tranche is checked by `scripts/termcheck.py`: every number must appear in the filing, CUSIP/ISIN check digits must be valid, the yield recomputed from the price must match, spread must equal yield minus benchmark yield, net proceeds must add up, and the currency must match the symbol on the principal amount. Non-debt FWPs (equity offerings) are skipped before the model sees them.

**3. Promote and re-run the analytics**

```bash
python scripts/promote.py             # approved, non-FAIL rows -> tranches.csv
python scripts/treasury.py
python scripts/reconcile.py
```

**Adding tranches by hand.** Notes without an FWP pricing term sheet go into `data/tranches.csv` directly. Fill in `source_filing` and `source_url` for every row; if a number can't be traced to a document, it doesn't go in.

| Source | What it gives you |
|---|---|
| 424B2 prospectus supplements | Exact coupon, size, maturity, and pricing for USD deals |
| 8-K filings around issuance dates | Underwriting agreements and note forms, often with foreign-currency deal terms |
| 10-K / 10-Q debt footnote | Outstanding notes by currency, coupon ranges, total carrying value |
| Bloomberg (Ross terminals) | Tranche-level data for non-USD deals, yields and spreads at issue |

**Reconciliation.** The tranche total should land close to reported `LongTermDebt`. Differences come from FX, discounts and issuance costs, notes issued after the XBRL period end (reported separately), and any notes you've missed. Chase down anything large.

**Checks.** Offline tests: `pytest`. Extraction regression against hand-verified values (writes nothing to the pending or audit files):

```bash
python scripts/extract_terms.py --url https://www.sec.gov/Archives/edgar/data/1652044/000119312525100802/d806252dfwp.htm --golden tests/golden/2025-04-28_usd.csv
```

## Data files

| File | Contents |
|---|---|
| `data/tranches.csv` | Your real database. |
| `data/fx_rates.csv` | USD per unit of each currency |
| `data/market_yields.csv` | Your refinancing yield estimate by currency (used by `treasury.py`) |
| `data/treasury_curve.csv` | Latest U.S. Treasury par yield curve, one row per tenor |
| `data/tranches_pending.csv` | Extracted tranches awaiting your review |
| `data/extraction_audit.jsonl` | Raw model output, issues, and evidence per processed FWP |

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
