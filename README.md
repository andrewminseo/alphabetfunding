# Alphabet Treasury Lab

Alphabet's bond debt has grown from $49.1B at the end of 2025 to $122.8B tracked today, about 2.5 times, after more than $75B of new notes in 2026. That borrowing now spans six currencies: sterling, Swiss franc, Canadian dollar and yen notes were all first issued this year, alongside USD and EUR. Deals are getting larger. The USD deals went from $5.0B in May 2025 to $17.5B in November 2025, $20.0B in February 2026 and $25.0B in August 2026. Refinancing is expensive too: the $30.3B maturing within five years carries an average rate of about 3.5%, and replacing it would raise annual interest cost by about $190M even if yields fell 100bp.

The open question is the June 2026 equity raise. Alphabet raised about $49.5B net: $20.5B of common stock in a public offering, $10.0B in a private placement with a Berkshire Hathaway affiliate, and $19.0B of 6.25% mandatory convertible preferred stock (10-Q for June 30, 2026; underwriters' overallotment options were exercised in full, per the June 4 8-K). A separate $40B at-the-market program had no sales by June 30. It did this in the same quarter as its EUR, CAD and JPY bond deals. What this says about how much more debt Alphabet intends to carry isn't something the bond data can answer.

This repo is a treasury monitor built from public data. It reconstructs Alphabet's bond portfolio from SEC filings, one row per note, and uses that to look at debt outstanding, currency mix, maturities, interest cost, and refinancing exposure.

## Current snapshot

As of September 29, 2026, from `scripts/treasury.py` (face value, non-USD notes converted at September 23 ECB rates, floating-rate notes costed at SOFR plus their margin):

| | |
|---|---|
| Debt tracked | $122.8B |
| Rows | 68 (67 notes and one CHF series, see below) |
| Weighted average coupon | 4.32% |
| Weighted average maturity | 16.3 years |
| Annual coupon cost | $5.3B |
| Maturing within five years | $30.3B |

Currency mix: USD 62.3%, EUR 20.8%, GBP 6.0%, CAD 4.9%, CHF 3.0%, JPY 3.0%.

The Swiss franc notes (issued February 2026) were sold in Switzerland and not registered with the SEC, so EDGAR only has the 10-Q's summary: a 1.06% weighted average coupon, about 10 years weighted average maturity, and maturities from 2029 to 2051. They are in the database as one series-level row at 1.06% maturing in 2036, and show up as a single 2036 bar in the maturity ladder. The 10-Q text rounds the principal to CHF 3.1B. The row uses CHF 3,059M instead, the average of the amounts implied by the 10-Q's USD face values at March 31 ($3,833M, CHF 3,064.9M) and June 30 ($3,772M, CHF 3,053.5M) at ECB rates. The two differ by 0.37%, attributed to Alphabet translating at its own FX rates.

The script also writes a maturity ladder by currency (`output/maturity_ladder.png`) and a refinancing sensitivity table (`output/refi_sensitivity.csv`).

Refinancing the notes that mature within five years raises annual interest cost even if yields fall 100bp. Those notes carry an average rate of about 3.5%, below the refinancing yields used here (the latest deal in each currency). CAD and JPY are the exceptions: at -100bp their refinancing yields fall below the coupons on their maturing notes. The $1.75B of floating-rate notes is included at the NY Fed 30-day Average SOFR (3.705% on September 25, 2026) plus each note's margin.

## Reconciliation

As a cross-check, the database is compared with the total face value of long-term debt Alphabet reports (XBRL `LongTermDebt` in the 10-K, `DebtInstrumentCarryingAmount` in 10-Qs). At June 30, 2026, the latest 10-Q:

- The database has $99.42B of face value across 59 rows outstanding on that date, converted at June 30, 2026 FX rates.
- Alphabet reported $101.08B, which includes $1.69B of other long-term debt (credit facilities) that isn't in the note database. That leaves $99.40B of notes.
- The gap is +$0.02B (under 0.1%), from using ECB rates rather than Alphabet's own. The CHF row adds about $7M of that.

The CHF row isn't an independent check. Its principal is derived from the USD amounts in the 10-Qs, so it matches the reported figure almost by construction.

At December 31, 2025 the gap was about -$16M, all from the EUR rate. The 10-K implies about 1.1762 USD per euro; the ECB rate for that date is 1.1750.

Another $25.00B across 10 notes was issued after June 30, 2026 and is outside that comparison.

The two figures aren't expected to match exactly. Missing notes, FX translation, and differences between face value and reported amounts all affect the comparison.

## How the database is built

```
SEC filings -> extraction -> validation -> my review -> tranche database -> analysis
```

Most notes come from the pricing term sheets (FWP filings) Alphabet files for each bond deal. A local language model reads each term sheet and fills in the terms: size, coupon, maturity, price, yield, spread, benchmark, and CUSIP.

Extracted terms are not accepted automatically. Each note is checked against the source filing:

- every number has to appear in the filing, and size, coupon, and maturity have to appear under that note's own label
- the yield recalculated from the price, on the coupon schedule stated in the filing, has to match the stated yield
- the spread has to equal yield minus benchmark yield
- net proceeds have to equal principal times price less the underwriting discount
- CUSIP and ISIN check digits have to be valid

Each note is marked PASS, WARN, or FAIL and goes into a pending file. I review each one against the filing, and only notes I approve are added to `data/tranches.csv`. FAIL notes can't be promoted.

Notes without a pricing term sheet can be added by hand from the prospectus supplement or the 10-K/10-Q debt footnote. Every row carries its source filing and URL. If a number can't be traced to a document, it doesn't go in.

## Limitations

- **Refinancing sensitivity.** Only currencies with a refinancing yield in `data/market_yields.csv` are included.
  - Each currency uses the principal-weighted re-offer yield of Alphabet's most recent deal in that currency (for USD, the fixed-rate notes from the August 2026 deal). These are new-issue yields at the time of each deal, not current market levels.
  - This assumes maturing notes are replaced at the same maturity mix as that latest deal.
  - Each note is assumed to be refinanced 1:1 in the same currency, with FX held constant and no swaps.
- **Face value.** The analysis uses face value, not carrying value.
- **FX.** The current snapshot uses one FX snapshot, not issue-date rates.
- **Coverage.** Coverage is limited to notes that are in the database. See the reconciliation above.
- **CHF series.** The CHF notes are one row built from 10-Q averages, not individual notes. Their maturities in the ladder and their coupon are approximations until the Swiss offering terms are sourced. There's no CHF refinancing yield yet.

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
