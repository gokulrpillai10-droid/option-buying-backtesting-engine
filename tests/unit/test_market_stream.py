"""MarketData.stream() — expiry-batched iterator that loads each parquet exactly once.

The driver consumes (expiry, {day: Chain}) batches. The 'load once' guarantee is the
whole point of ARCH-4 (84GB store can't be reloaded per day). Verified by counting reads.
"""
from datetime import date, datetime

import pandas as pd

from tests.conftest import make_row, CHAIN_COLUMNS

from engine.data.spot import SpotSeries
from engine.data.market import MarketData


class FakeCatalog:
    def __init__(self, paths):          # paths: {expiry_iso: filepath}
        self._paths = paths

    def expiries(self):
        return list(self._paths)

    def path_for(self, iso):
        return self._paths.get(iso)


def _spot(dates):
    rows = [(datetime.combine(d, datetime.min.time()).replace(hour=9, minute=15),
             100.0, 100.0, 100.0, 100.0) for d in dates]
    return SpotSeries(pd.DataFrame(rows, columns=["DateTime", "Open", "High", "Low", "Close"]))


def _two_day_option_parquet(tmp_path):
    rows = []
    for d in (date(2024, 1, 2), date(2024, 1, 3)):
        for m in (15, 16):
            rows.append(make_row(datetime(d.year, d.month, d.day, 9, m), 20000,
                                 call_open=100 + m, call_close=100 + m))
    p = tmp_path / "options.parquet"
    pd.DataFrame(rows, columns=CHAIN_COLUMNS).to_parquet(p)
    return str(p)


def test_stream_loads_each_expiry_once_and_yields_day_chains(tmp_path):
    p = _two_day_option_parquet(tmp_path)
    loads = {"n": 0}

    def reader(path):
        loads["n"] += 1
        return pd.read_parquet(path)

    cat = FakeCatalog({"2024-01-04": p})
    md = MarketData(cat, _spot([date(2024, 1, 2), date(2024, 1, 3)]), min_dte=1, reader=reader)

    batches = list(md.stream(date(2024, 1, 2), date(2024, 1, 3)))
    assert len(batches) == 1
    exp, chains = batches[0]
    assert exp == date(2024, 1, 4)
    assert set(chains) == {date(2024, 1, 2), date(2024, 1, 3)}

    # each value is a real, working Chain
    snap = chains[date(2024, 1, 2)].snapshot_at(datetime(2024, 1, 2, 9, 16))
    assert snap.call(20000).close == 116.0

    assert loads["n"] == 1               # the load-once guarantee


def test_stream_skips_expiry_with_no_path(tmp_path):
    cat = FakeCatalog({"2024-01-04": None})
    md = MarketData(cat, _spot([date(2024, 1, 2)]), min_dte=1)
    assert list(md.stream(date(2024, 1, 2), date(2024, 1, 2))) == []
