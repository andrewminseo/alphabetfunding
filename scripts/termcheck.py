"""
termcheck.py

Deterministic checks for bond terms extracted from a filing. No network, no LLM.

Every tranche an extractor produces goes through check_tranche(), which returns
PASS / WARN / FAIL plus a list of issues and, for each field, the snippet of the
source text where its value was found (the evidence).

Checks:
  - grounding: every number, date, and identifier must appear in the source text
  - series-tied grounding: maturity, principal, and coupon must appear under the
    tranche's own series label in their labeled section
  - CUSIP and ISIN check digits, and ISIN must embed the CUSIP for US ISINs
  - yield recomputed from price, coupon, and dates must match the stated yield
  - spread must equal issue yield minus benchmark yield
  - net proceeds must equal principal x (price - underwriting discount)
  - currency must match the symbol or code right before the principal amount
  - floating notes: coupon and spread must be null; margin may be written as a percent
  - a spread matching another tranche's yield minus benchmark (check_deal)
  - required fields present, maturity after settlement, values in sane ranges

Yield convention: dated date = settlement date (new issue, no accrued interest),
coupons on a regular schedule rolled back from maturity, first coupon prorated,
compounding at the coupon frequency. 30/360 for USD, ACT/ACT (ICMA) otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

# Tolerances, in basis points unless noted.
YIELD_OK_BP, YIELD_WARN_BP = 1.0, 3.0
SPREAD_OK_BP, SPREAD_WARN_BP = 1.0, 3.0
PROCEEDS_TOL = 1e-4  # fraction of principal

NUMERIC_FIELDS = [
    "principal", "coupon_pct", "issue_price_pct", "underwriting_discount_pct",
    "net_proceeds", "issue_yield_pct", "spread_bps", "benchmark_yield_pct",
    "floating_margin_bps",
]
DATE_FIELDS = ["maturity_date"]
ID_FIELDS = ["cusip", "isin"]


@dataclass
class Issue:
    level: str   # WARN or FAIL
    field: str
    message: str


@dataclass
class CheckResult:
    status: str = PASS
    issues: list[Issue] = field(default_factory=list)
    evidence: dict[str, str | None] = field(default_factory=dict)

    def add(self, level: str, fld: str, message: str) -> None:
        self.issues.append(Issue(level, fld, message))
        if level == FAIL or (level == WARN and self.status == PASS):
            self.status = level


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------

def _char_value(c: str) -> int:
    if c.isdigit():
        return int(c)
    if c.isalpha():
        return ord(c.upper()) - ord("A") + 10
    return {"*": 36, "@": 37, "#": 38}[c]


def cusip_check_digit(base8: str) -> str:
    total = 0
    for i, c in enumerate(base8.upper()):
        v = _char_value(c)
        if i % 2 == 1:
            v *= 2
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10)


def valid_cusip(cusip: str) -> bool:
    c = re.sub(r"\s", "", cusip or "").upper()
    return bool(re.fullmatch(r"[0-9A-Z*@#]{8}[0-9]", c)) and cusip_check_digit(c[:8]) == c[8]


def valid_isin(isin: str) -> bool:
    s = re.sub(r"\s", "", isin or "").upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9A-Z]{9}[0-9]", s):
        return False
    digits = "".join(str(_char_value(c)) for c in s[:-1])
    total = 0
    for i, d in enumerate(reversed(digits)):
        v = int(d) * (2 if i % 2 == 0 else 1)
        total += v // 10 + v % 10
    return str((10 - total % 10) % 10) == s[-1]


# --------------------------------------------------------------------------
# Yield math
# --------------------------------------------------------------------------

def _to_date(d) -> date:
    return d if isinstance(d, date) else date.fromisoformat(str(d)[:10])


def _add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def _days_30360(a: date, b: date) -> int:
    d1 = min(a.day, 30)
    d2 = b.day if (b.day < 31 or d1 < 30) else 30
    return 360 * (b.year - a.year) + 30 * (b.month - a.month) + (d2 - d1)


def _schedule(settle: date, maturity: date, freq: int) -> tuple[date, list[date]]:
    """Previous quasi-coupon date and coupon dates after settlement."""
    step = 12 // freq
    dates = [maturity]
    n = 0
    while dates[-1] > settle:
        n += 1
        dates.append(_add_months(maturity, -step * n))
    prev = dates[-1]
    return prev, sorted(dates[:-1])


def price_from_yield(yield_pct: float, coupon_pct: float, settle, maturity,
                     freq: int = 2, day_count: str = "30/360") -> float:
    settle, maturity = _to_date(settle), _to_date(maturity)
    prev, coupons = _schedule(settle, maturity, freq)
    nxt = coupons[0]
    if day_count == "30/360":
        frac = _days_30360(settle, nxt) / (360 / freq)
    else:
        frac = (nxt - settle).days / (nxt - prev).days
    c = coupon_pct / freq
    y = yield_pct / 100 / freq
    pv = 0.0
    for k, _ in enumerate(coupons):
        t = frac + k
        cf = c * frac if k == 0 else c
        if k == len(coupons) - 1:
            cf += 100
        pv += cf / (1 + y) ** t
    return pv


YIELD_LO, YIELD_HI = -5.0, 50.0


def yield_from_price(price_pct: float, coupon_pct: float, settle, maturity,
                     freq: int = 2, day_count: str = "30/360") -> float | None:
    """Yield to maturity in percent, by bisection. None if no yield in
    [YIELD_LO, YIELD_HI] reproduces the price."""
    lo, hi = YIELD_LO, YIELD_HI
    p_lo = price_from_yield(lo, coupon_pct, settle, maturity, freq, day_count)
    p_hi = price_from_yield(hi, coupon_pct, settle, maturity, freq, day_count)
    if not (p_hi <= price_pct <= p_lo):
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if price_from_yield(mid, coupon_pct, settle, maturity, freq, day_count) > price_pct:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# --------------------------------------------------------------------------
# Grounding
# --------------------------------------------------------------------------

_NUM_RE = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?!\d)(\s*(?:million|billion))?",
    re.IGNORECASE,
)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " "))


def _snippet(text: str, start: int, end: int, pad: int = 60) -> str:
    return text[max(0, start - pad):min(len(text), end + pad)].strip()


def find_number(text: str, value) -> str | None:
    """Snippet around the first occurrence of `value` as a number in text."""
    try:
        target = Decimal(str(value))
    except InvalidOperation:
        return None
    for m in _NUM_RE.finditer(text):
        n = Decimal(m.group(1).replace(",", "") + (m.group(2) or ""))
        scale = (m.group(3) or "").strip().lower()
        if scale == "million":
            n *= 1_000_000
        elif scale == "billion":
            n *= 1_000_000_000
        if n == target:
            return _snippet(text, m.start(), m.end())
    return None


# Longest first, so "C$" wins over "$".
CURRENCY_MARKERS = [("C$", "CAD"), ("CHF", "CHF"), ("JPY", "JPY"), ("$", "USD"),
                    ("€", "EUR"), ("£", "GBP"), ("¥", "JPY")]


def currency_before_number(text: str, value) -> tuple[str | None, str | None]:
    """(currency code, snippet) from the symbol or code immediately before the
    first occurrence of `value` that has one. (None, None) if none do."""
    try:
        target = Decimal(str(value))
    except InvalidOperation:
        return None, None
    for m in _NUM_RE.finditer(text):
        n = Decimal(m.group(1).replace(",", "") + (m.group(2) or ""))
        scale = (m.group(3) or "").strip().lower()
        n *= {"million": 1_000_000, "billion": 1_000_000_000}.get(scale, 1)
        if n != target:
            continue
        before = text[max(0, m.start() - 6):m.start()].rstrip()
        for marker, code in CURRENCY_MARKERS:
            if before.upper().endswith(marker):
                return code, _snippet(text, m.start(), m.end())
    return None, None


def find_percent_as_bps(text: str, bps) -> str | None:
    """Snippet where `bps` basis points is written as a percent ("0.52%" for 52)."""
    try:
        target = Decimal(str(bps)) / 100
    except InvalidOperation:
        return None
    for m in _NUM_RE.finditer(text):
        if m.group(3):
            continue
        n = Decimal(m.group(1).replace(",", "") + (m.group(2) or ""))
        if n == target and text[m.end():].lstrip().startswith("%"):
            return _snippet(text, m.start(), m.end())
    return None


# Series-tied grounding ------------------------------------------------------

SECTIONS = {
    "maturity_date": r"Maturity Date",
    "principal": r"Aggregate Principal Amount",
    "coupon_pct": r"Coupon(?: \(Interest Rate\))?|Interest Rate",
}
# A row label: capitalized words, no digits or commas ("Coupon (Interest Rate): |").
_ROW_LABEL = re.compile(r"(?:^|(?<=\s))[A-Z][A-Za-z ()/&'’*\-]{0,80}:\s*\|")
_SERIES = re.compile(r"(\d{4})\s+(Floating\s+Rate\s+)?Notes\s*:", re.IGNORECASE)
_HEADING = re.compile(r"(Floating\s+Rate\s+)?Notes\s+due\s+(\d{4})", re.IGNORECASE)
# Day may carry an ordinal suffix, sometimes split off by a superscript ("15 th ,").
_ORD = r"(?:\s?(?:st|nd|rd|th))?"
_DATE_RE = re.compile(rf"[A-Z][a-z]+\s+\d{{1,2}}{_ORD}\s*,?\s+\d{{4}}|\d{{1,2}}{_ORD}\s+[A-Z][a-z]+\s+\d{{4}}")


def series_key(label: str | None) -> tuple[str, bool] | None:
    """("2046", False) for "2046 Notes"; ("2028", True) for "2028 Floating Rate
    Notes" or "Floating Rate Notes due 2028". None if no year."""
    if not label:
        return None
    m = re.search(r"\d{4}", label)
    return (m.group(0), "floating" in label.lower()) if m else None


def section_entries(text: str, field: str) -> dict[tuple[str, bool], str] | None:
    """{series key: entry text} for every labeled section of `field` in the
    (normalized) text. An unlabeled section takes its key from the nearest
    preceding "... Notes due YYYY" heading. None if the section is absent."""
    found = False
    entries: dict[tuple[str, bool], str] = {}
    for m in re.finditer(rf"(?:^|(?<=\s))(?:{SECTIONS[field]})\s*:\s*\|", text):
        found = True
        nxt = _ROW_LABEL.search(text, m.end())
        body = text[m.end():nxt.start() if nxt else len(text)]
        parts = list(_SERIES.finditer(body))
        if parts:
            for i, p in enumerate(parts):
                end = parts[i + 1].start() if i + 1 < len(parts) else len(body)
                entries.setdefault((p.group(1), bool(p.group(2))), body[p.end():end].strip())
        else:
            heads = list(_HEADING.finditer(text, max(0, m.start() - 400), m.start()))
            if heads:
                h = heads[-1]
                entries.setdefault((h.group(2), bool(h.group(1))), body.strip())
    return entries if found else None


def _entry_matches(field: str, entry: str, value) -> bool:
    if field == "maturity_date":
        want = _to_date(value)
        for d in _DATE_RE.findall(entry):
            try:
                return date.fromisoformat(pd_date(d)) == want
            except ValueError:
                continue
        return False
    m = _NUM_RE.search(entry)
    if not m:
        return False
    n = Decimal(m.group(1).replace(",", "") + (m.group(2) or ""))
    n *= {"million": 1_000_000, "billion": 1_000_000_000}.get((m.group(3) or "").strip().lower(), 1)
    return n == Decimal(str(value))


def pd_date(s: str) -> str:
    from datetime import datetime
    s = re.sub(r"(\d)\s?(?:st|nd|rd|th)\b", r"\1", s.replace(",", ""))
    s = re.sub(r"\s+", " ", s).strip()
    for fmt in ("%B %d %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    raise ValueError(s)


def check_series_fields(t: dict, text: str, r: "CheckResult") -> None:
    key = series_key(t.get("series_label"))
    for field in SECTIONS:
        value = t.get(field)
        if value is None:
            continue
        entries = section_entries(text, field)
        name = SECTIONS[field].split("(")[0].split("|")[0].strip()
        if entries is None:
            r.add(WARN, field, f'"{name}:" section not found; value not tied to its series')
            continue
        if key is None or key not in entries:
            r.add(WARN, field, f'series label {t.get("series_label")!r} not found under "{name}:"')
            continue
        entry = entries[key]
        r.evidence[f"{field}_series"] = entry[:120]
        if not _entry_matches(field, entry, value):
            r.add(FAIL, field, f'{value} differs from "{name}:" for {t.get("series_label")}: {entry[:80]!r}')


def find_date(text: str, value) -> str | None:
    d = _to_date(value)
    month = d.strftime("%B")
    patterns = [
        rf"{month}\s+{d.day}{_ORD}\s*,?\s+{d.year}",
        rf"{d.day}{_ORD}\s+{month},?\s+{d.year}",
        re.escape(d.isoformat()),
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return _snippet(text, m.start(), m.end())
    return None


def find_identifier(text: str, value: str) -> str | None:
    """Match an identifier even if the filing splits it with spaces (02079K AK3)."""
    ident = re.sub(r"\s", "", value).upper()
    pattern = r"\s?".join(re.escape(c) for c in ident)
    m = re.search(pattern, text, re.IGNORECASE)
    return _snippet(text, m.start(), m.end()) if m else None


# --------------------------------------------------------------------------
# The check
# --------------------------------------------------------------------------

def _num(x):
    return None if x is None else float(x)


def check_tranche(tranche: dict, text: str, *, currency: str, settlement_date,
                  freq: int, day_count: str | None = None,
                  yield_basis: str | None = None) -> CheckResult:
    """Check one extracted tranche against its source text.

    tranche: dict with the extractor's per-tranche fields (principal, coupon_pct,
      maturity_date, issue_price_pct, underwriting_discount_pct, net_proceeds,
      issue_yield_pct, spread_bps, benchmark_yield_pct, floating_index,
      floating_margin_bps, cusip, isin, rate_type). Missing keys count as null.
    text: the filing text the values were extracted from.
    yield_basis: "semi-annual" when the filing labels the stated yield as
      semi-annual (e.g. "Yield to Maturity (Semi-Annual / Annual)"); the yield
      from price is then converted to semi-annual before comparing. None means
      the stated yield compounds at the coupon frequency.
    """
    r = CheckResult()
    t = {k: tranche.get(k) for k in set(tranche) | set(NUMERIC_FIELDS + DATE_FIELDS + ID_FIELDS)}
    text = normalize_text(text)
    currency = (currency or "").upper()
    day_count = day_count or ("30/360" if currency == "USD" else "ACT/ACT")
    floating = (t.get("rate_type") or "").lower() == "floating"

    # Required fields
    if not currency:
        r.add(FAIL, "currency", "missing")
    if not settlement_date:
        r.add(FAIL, "settlement_date", "missing")
    for f in ["principal", "maturity_date"] + ([] if floating else ["coupon_pct"]):
        if t.get(f) is None:
            r.add(FAIL, f, "missing")
    if floating and not t.get("floating_index"):
        r.add(FAIL, "floating_index", "floating note without an index")
    if floating and t.get("coupon_pct") is not None:
        r.add(FAIL, "coupon_pct", "must be null for a floating note")
    if floating and t.get("spread_bps") is not None:
        r.add(FAIL, "spread_bps", "must be null for a floating note (margin goes in floating_margin_bps)")

    # Grounding
    for f in NUMERIC_FIELDS:
        if t.get(f) is None:
            continue
        ev = find_number(text, t[f])
        if ev is None and f == "floating_margin_bps":
            ev = find_percent_as_bps(text, t[f])
        r.evidence[f] = ev
        if ev is None:
            r.add(FAIL, f, f"value {t[f]} not found in filing text")
    for f in DATE_FIELDS:
        if t.get(f) is None:
            continue
        try:
            ev = find_date(text, t[f])
        except ValueError:
            r.add(FAIL, f, f"not an ISO date: {t[f]!r}")
            continue
        r.evidence[f] = ev
        if ev is None:
            r.add(FAIL, f, f"date {t[f]} not found in filing text")
    for f in ID_FIELDS:
        if not t.get(f):
            continue
        ev = find_identifier(text, t[f])
        r.evidence[f] = ev
        if ev is None:
            r.add(FAIL, f, f"{t[f]} not found in filing text")

    # Currency: symbol or code right before the principal amount
    if currency and t.get("principal") is not None and r.evidence.get("principal"):
        found, ev = currency_before_number(text, t["principal"])
        r.evidence["currency"] = ev
        if found is None:
            r.add(WARN, "currency", "no currency symbol before the principal amount")
        elif found != currency:
            r.add(FAIL, "currency", f"extracted {currency} but principal is shown in {found}")

    # Series-tied grounding: value must sit under its own series label
    check_series_fields(t, text, r)

    # Identifiers
    if t.get("cusip") and not valid_cusip(t["cusip"]):
        r.add(FAIL, "cusip", f"bad check digit: {t['cusip']}")
    if t.get("isin"):
        if not valid_isin(t["isin"]):
            r.add(FAIL, "isin", f"bad check digit: {t['isin']}")
        elif t.get("cusip") and t["isin"].upper().startswith("US"):
            if t["isin"].upper()[2:11] != re.sub(r"\s", "", t["cusip"]).upper():
                r.add(FAIL, "isin", "ISIN does not embed the CUSIP")

    # Dates
    settle = mat = None
    try:
        settle = _to_date(settlement_date) if settlement_date else None
        mat = _to_date(t["maturity_date"]) if t.get("maturity_date") else None
    except ValueError:
        pass
    if settle and mat and mat <= settle:
        r.add(FAIL, "maturity_date", "maturity is not after settlement")

    # Ranges
    price, cpn, yld = _num(t.get("issue_price_pct")), _num(t.get("coupon_pct")), _num(t.get("issue_yield_pct"))
    if price is not None and not 90 <= price <= 101:
        r.add(WARN, "issue_price_pct", f"unusual issue price {price}")
    if cpn is not None and not 0 <= cpn <= 15:
        r.add(FAIL, "coupon_pct", f"implausible coupon {cpn}")
    if _num(t.get("principal")) is not None and t["principal"] <= 0:
        r.add(FAIL, "principal", "non-positive principal")

    # Yield from price
    if not floating and None not in (price, cpn, settle, mat) and mat > settle:
        calc = yield_from_price(price, cpn, settle, mat, freq, day_count)
    else:
        calc = None
    if calc is None and not floating and None not in (price, cpn, settle, mat) and mat > settle:
        r.add(FAIL, "issue_yield_pct", f"no yield solves this price ({price})")
    elif calc is not None:
        if yield_basis == "semi-annual" and freq != 2:
            r.evidence["yield_from_price_coupon_basis"] = f"{calc:.4f}"
            calc = 2 * ((1 + calc / 100 / freq) ** (freq / 2) - 1) * 100
        r.evidence["yield_basis"] = yield_basis or f"coupon frequency ({freq}/yr)"
        r.evidence["yield_from_price"] = f"{calc:.4f}"
        if yld is None:
            r.add(WARN, "issue_yield_pct", f"no stated yield; price implies {calc:.3f}")
        else:
            diff = abs(calc - yld) * 100
            if diff > YIELD_WARN_BP:
                r.add(FAIL, "issue_yield_pct", f"stated {yld} vs {calc:.3f} from price ({diff:.1f} bp)")
            elif diff > YIELD_OK_BP:
                r.add(WARN, "issue_yield_pct", f"stated {yld} vs {calc:.3f} from price ({diff:.1f} bp)")
    elif not floating and price is None:
        r.add(WARN, "issue_price_pct", "no issue price; yield not verifiable")

    # Spread = yield - benchmark yield
    spread, bmk = _num(t.get("spread_bps")), _num(t.get("benchmark_yield_pct"))
    if None not in (spread, bmk, yld):
        implied = (yld - bmk) * 100
        r.evidence["spread_from_yields"] = f"{implied:.1f}"
        diff = abs(implied - spread)
        if diff > SPREAD_WARN_BP:
            r.add(FAIL, "spread_bps", f"stated {spread:g} bp vs yield - benchmark = {implied:.1f} bp")
        elif diff > SPREAD_OK_BP:
            r.add(WARN, "spread_bps", f"stated {spread:g} bp vs yield - benchmark = {implied:.1f} bp")
    elif spread is not None and not floating:
        r.add(WARN, "spread_bps", "spread not verifiable (missing yield or benchmark yield)")
    elif spread is None and not floating and None not in (bmk, yld):
        implied = (yld - bmk) * 100
        r.evidence["spread_from_yields"] = f"{implied:.1f}"
        r.add(WARN, "spread_bps", f"spread missing; yield minus benchmark = {implied:.1f} bp")

    # Net proceeds
    prin, disc, net = _num(t.get("principal")), _num(t.get("underwriting_discount_pct")), _num(t.get("net_proceeds"))
    if None not in (prin, price, disc, net):
        calc = prin * (price - disc) / 100
        r.evidence["net_proceeds_calc"] = f"{calc:,.2f}"
        if abs(calc - net) > prin * PROCEEDS_TOL:
            r.add(FAIL, "net_proceeds", f"stated {net:,.0f} vs principal x (price - discount) = {calc:,.0f}")

    return r


def _implied_spread(t: dict) -> float | None:
    y, b = _num(t.get("issue_yield_pct")), _num(t.get("benchmark_yield_pct"))
    return None if None in (y, b) else (y - b) * 100


def check_deal(tranches: list[dict], results: list[CheckResult]) -> None:
    """Cross-tranche checks; adds issues to `results` in place.

    A tranche whose spread matches another tranche's yield minus benchmark, but
    not its own, was probably copied from the wrong series: FAIL if its own
    yield minus benchmark is known (or it is a floating note), else WARN.
    """
    implied = [_implied_spread(t) for t in tranches]
    for i, (t, r) in enumerate(zip(tranches, results)):
        spread = _num(t.get("spread_bps"))
        if spread is None:
            continue
        own = implied[i]
        if own is not None and abs(own - spread) <= SPREAD_OK_BP:
            continue
        for j, other in enumerate(implied):
            if j == i or other is None or abs(other - spread) > SPREAD_OK_BP:
                continue
            label = tranches[j].get("series_label") or tranches[j].get("maturity_date") or f"tranche {j + 1}"
            floating = (t.get("rate_type") or "").lower() == "floating"
            level = FAIL if (own is not None or floating) else WARN
            r.add(level, "spread_bps",
                  f"spread {spread:g} bp matches {label}'s yield minus benchmark ({other:.1f} bp), not this tranche's")
            break
