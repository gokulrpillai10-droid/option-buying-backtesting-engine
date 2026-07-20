import re
from pathlib import Path

from strategies import gap_fill_params as p


def test_params_match_prestudy_out():
    """The committed constants must equal the pre-study's recorded FROZEN line."""
    out = Path("analysis/gap_fill_premium_scale_out.txt").read_text()
    m = re.search(r"STOP=([\d.]+)\s+BOOK=([\d.]+)\s+TRAIL=([\d.]+)\s+FLOOR=([\d.]+)", out)
    assert m, "pre-study _out.txt missing FROZEN line"
    stop, book, trail, floor = (float(x) for x in m.groups())
    assert abs(p.STOP - stop) < 1e-6
    assert abs(p.BOOK - book) < 1e-6
    assert abs(p.TRAIL - trail) < 1e-6
    assert abs(p.FLOOR - floor) < 1e-9


def test_exit_sanity():
    assert p.STOP > 0 and p.BOOK > 0
    assert p.TRAIL == 0.5 * p.BOOK
    assert 0.0005 <= p.FLOOR <= p.CAP
