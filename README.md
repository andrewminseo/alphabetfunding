# Alphabet Treasury Lab

A treasury monitor for Alphabet built from public data. It reconstructs Alphabet's bond portfolio from SEC filings, one row per note, and uses that to look at debt outstanding, currency mix, maturities, interest cost, and refinancing exposure.

## Current snapshot

As of September 25, 2026, from `scripts/treasury.py` (face value, non-USD notes converted at current FX):

| | |
|---|---|
| Debt tracked | $119.0B |
| Notes | 67 |
| Weighted average coupon | 4.43% |
| Weighted average maturity | 16.6 years |
| Annual coupon cost | $5.2B |
| Maturing within five years | $30.3B |

Currency mix: USD 64.3%, EUR 21.4%, GBP 6.2%, CAD 5.1%, JPY 3.1%.

The script also writes a maturity ladder by currency (`output/maturity_ladder.png`) and a refinancing sensitivity table (`output/refi_sensitivity.csv`).

Refinancing the notes that mature within five years raises annual interest cost even if yields fall 100bp. Those notes carry an average coupon of about 3.5%, below the refinancing yields used here (the latest deal in each currency). CAD and JPY are the exceptions: at -100bp their refinancing yields fall below the coupons on their maturing notes. The $1.8B of floating-rate notes is not in these figures (see Limitations).

## Reconciliation

As a cross-check, the database is compared with the long-term debt Alphabet reports in XBRL. At December 31, 2025:

- The database has $49.07B of face value across 29 notes outstanding on that date, converted at December 31, 2025 FX rates.
- Alphabet reported $49.09B of `LongTermDebt`, which matches the total face value in the 10-K debt footnote.
- The gap is about -$16M, from the EUR rate. The 10-K implies about 1.1762 USD per euro for its euro notes ($15,585M for €13,250M); the database uses the ECB reference rate for that date, 1.1750.

Every series in the 10-K debt footnote is now in the database. The last one added was the 2016 US dollar notes (1.998%, due August 2026), taken from the 2016 prospectus supplement and pricing term sheet. Those notes have since matured, so they count here but not in the current snapshot.

Another $72.87B across 39 notes was issued after December 31, 2025 and is outside that comparison.

The two figures aren't expected to match exactly. Missing notes, FX translation, and differences between face value and reported amounts all affect the comparison.

## How the database is built

```
SEC filings -> extraction -> validation -> my review -> tranche database -> analysis
```

Most notes come from the pricing term sheets (FWP filings) Alphabet files for each bond deal. A local language model reads each term sheet and fills in the terms: size, coupon, maturity, price, yield, spread, benchmark, and CUSIP.

Extracted terms are not accepted automatically. Each note is checked against the source filing:

- every number has to appear in the filing, and size, coupon, and maturity have to appear under that note's own label
- the yield recalculated from the price has to match the stated yield
- the spread has to equal yield minus benchmark yield
- net proceeds have to equal principal times price less the underwriting discount
- CUSIP and ISIN check digits have to be valid

Each note is marked PASS, WARN, or FAIL and goes into a pending file. I review each one against the filing, and only notes I approve are added to `data/tranches.csv`. FAIL notes can't be promoted.

Notes without a pricing term sheet can be added by hand from the prospectus supplement or the 10-K/10-Q debt footnote. Every row carries its source filing and URL. If a number can't be traced to a document, it doesn't go in.

## Limitations

- **Floating-rate notes.** $1.8B of floating-rate notes is left out of coupon cost and weighted average coupon until a SOFR rate is entered in `data/market_yields.csv`.
- **Refinancing sensitivity.** Only currencies with a refinancing yield in `data/market_yields.csv` are included.
  - Each currency uses the principal-weighted re-offer yield of Alphabet's most recent deal in that currency (for USD, the fixed-rate notes from the August 2026 deal). These are new-issue yields at the time of each deal, not current market levels.
  - This assumes maturing notes are replaced at the same maturity mix as that latest deal.
  - Each note is assumed to be refinanced 1:1 in the same currency, with FX held constant and no swaps.
- **Face value.** The analysis uses face value, not carrying value.
- **FX.** The current snapshot uses one FX snapshot, not issue-date rates.
- **Coverage.** Coverage is limited to notes that are in the database. See the reconciliation above.

## Data sources

- **SEC EDGAR:** filing index, pricing term sheets, and XBRL company facts (reported debt totals, debt maturities, interest expense)
- **FX:** ECB reference rates via the Frankfurter API, current and as of the reconciliation date
- **U.S. Treasury:** Daily Par Yield Curve Rates, saved to `data/treasury_curve.csv` for reference (not yet used in the calculations)
- **Refinancing yields:** entered by hand in `data/market_yields.csv`, with the source on each row

## Running it

```bash
pip install -r requirements.txt
export SEC_USER_AGENT="Your Name you@example.com"   # SEC requires name + email
ollama pull qwen2.5:14b                               # local model, needs ~10 GB free memory

python scripts/run_pipeline.py    # pull filings, extract new term sheets, update FX and
                                  # Treasury yields, run analysis and reconciliation
# review data/tranches_pending.csv; set approved = yes on rows you've checked
python scripts/promote.py         # approved rows -> data/tranches.csv
python scripts/treasury.py
```

On machines with less memory, `python scripts/extract_terms.py --index --model qwen2.5:7b` works but makes more mistakes, so more rows get flagged.

`pytest` runs the offline tests. To check extraction against a hand-verified deal (the April 2025 USD notes):

```bash
python scripts/extract_terms.py --golden tests/golden/2025-04-28_usd.csv \
  --url https://www.sec.gov/Archives/edgar/data/1652044/000119312525100802/d806252dfwp.htm
```

## Layout

```
data/tranches.csv          the note database
data/tranches_pending.csv  extracted notes awaiting review
data/market_yields.csv     refinancing yields and index rates
scripts/treasury.py        portfolio analytics
scripts/reconcile.py       comparison with reported debt
scripts/extract_terms.py   term-sheet extraction
scripts/termcheck.py       checks against the source filing
```
