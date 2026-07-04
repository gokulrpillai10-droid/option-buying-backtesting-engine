import shutil
from datetime import date, datetime
from pathlib import Path

import pytest

from engine.data.catalog import Catalog
from engine.data.chain import Chain
from engine.data.loader import DataLoader

FIXTURE = Path(__file__).parent.parent / "fixtures" / "mini_expiry_2023-12-07.parquet"


@pytest.fixture
def loader(tmp_path):
    part = (tmp_path / "Data 14" / "Data-v3" / "instrument=NIFTY50"
            / "OPTIONS" / "expiry=2023-12-07")
    part.mkdir(parents=True)
    shutil.copy(FIXTURE, part / "options_data.parquet")
    return DataLoader(Catalog(tmp_path))


def test_load_expiry_returns_chain_with_real_strikes(loader):
    chain = loader.load_expiry("2023-12-07")
    assert isinstance(chain, Chain)
    snap = chain.snapshot_at(datetime(2023, 11, 3, 9, 18))
    assert snap.strikes() == [19250, 19300, 19350, 19400, 19450]


def test_load_expiry_quotes_are_real_floats(loader):
    chain = loader.load_expiry("2023-12-07")
    snap = chain.snapshot_at(datetime(2023, 11, 3, 9, 18))
    q = snap.call(19350)
    assert q.open > 0
    assert isinstance(q.is_traded, bool)


def test_trading_dates_lists_distinct_session_days(loader):
    assert loader.trading_dates("2023-12-07") == [date(2023, 11, 3)]


def test_load_day_filters_to_one_session(loader):
    chain = loader.load_day("2023-12-07", date(2023, 11, 3))
    # 09:20 is in-session; a different day would be empty
    snap = chain.snapshot_at(datetime(2023, 11, 3, 9, 20))
    assert snap.strikes() == [19250, 19300, 19350, 19400, 19450]


def test_missing_expiry_raises(loader):
    with pytest.raises(KeyError):
        loader.load_expiry("1999-01-01")
