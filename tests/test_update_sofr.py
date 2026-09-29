import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update_sofr import set_sofr_row  # noqa: E402

HEADER = "currency,refi_yield_pct,as_of,source,rate_type,index_name\n"
REFI = 'USD,5.665,2026-08-10,"Deal yield, 8 tranches",refi,\n'
SOFR = "USD,3.70500,2026-09-25,old,index,SOFR\n"


def test_updates_only_the_sofr_row():
    out = set_sofr_row(HEADER + REFI + SOFR, 3.73909, "2026-09-29")
    lines = out.splitlines()
    assert lines[0] + "\n" == HEADER
    assert lines[1] + "\n" == REFI  # refi row untouched, quoting kept
    assert lines[2].startswith("USD,3.73909,2026-09-29,")
    assert lines[2].endswith(",index,SOFR")


def test_refi_row_labelled_sofr_is_not_touched():
    with pytest.raises(ValueError, match="found 0"):
        set_sofr_row(HEADER + "USD,5.0,2026-01-01,x,refi,SOFR\n", 3.7, "2026-09-29")


def test_duplicate_sofr_rows_rejected():
    with pytest.raises(ValueError, match="found 2"):
        set_sofr_row(HEADER + SOFR + SOFR, 3.7, "2026-09-29")
