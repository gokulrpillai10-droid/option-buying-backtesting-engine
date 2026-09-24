import warnings
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from engine.data.catalog import Catalog
from engine.instruments import LotTable


def _make_partition(root: Path, folder: str, expiry: str) -> Path:
    p = root / folder / "Data-v3" / "instrument=NIFTY50" / "OPTIONS" / f"expiry={expiry}"
    p.mkdir(parents=True, exist_ok=True)
    f = p / "options_data.parquet"
    f.write_bytes(b"")  # discovery only cares about the path
    return f


def test_discovers_expiries_across_multiple_data_folders(tmp_path):
    _make_partition(tmp_path, "Data 14", "2023-12-07")
    _make_partition(tmp_path, "Data 25", "2024-02-29")
    cat = Catalog(tmp_path)
    assert cat.expiries() == ["2023-12-07", "2024-02-29"]


def test_path_for_returns_the_parquet(tmp_path):
    f = _make_partition(tmp_path, "Data 14", "2023-12-07")
    cat = Catalog(tmp_path)
    assert cat.path_for("2023-12-07") == f


def test_deduplicates_expiry_present_in_two_folders(tmp_path):
    _make_partition(tmp_path, "Data 14", "2023-12-07")
    _make_partition(tmp_path, "Data 25", "2023-12-07")
    cat = Catalog(tmp_path)
    assert cat.expiries() == ["2023-12-07"]


# ---- LotTable anomaly guard ----

def _lot_table(by: dict) -> LotTable:
    """Build a LotTable directly from a {date_str: lot} dict."""
    rows = [(pd.Timestamp(d), lot) for d, lot in by.items()]
    df = pd.DataFrame(rows, columns=["Expiry", "LotSize"])
    return LotTable.from_frame(df)


def test_lot_anomaly_isolated_spike_is_corrected():
    # 2025-01-30 is 25 but surrounded by 75s — the known data entry bug.
    with pytest.warns(UserWarning, match="isolated lot-size anomaly"):
        tbl = _lot_table({
            "2025-01-09": 75,
            "2025-01-16": 75,
            "2025-01-23": 75,
            "2025-01-30": 25,   # bug
            "2025-02-06": 75,
            "2025-02-13": 75,
        })
    assert tbl.for_expiry(date(2025, 1, 30)) == 75   # corrected


def test_lot_anomaly_genuine_regime_transition_not_flagged():
    # 25→75 genuine transition: the first 75 after a run of 25s must NOT be flagged.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        tbl = _lot_table({
            "2024-12-12": 25,
            "2024-12-19": 25,
            "2025-01-02": 75,   # real transition — neighbors disagree (25 vs 75)
            "2025-01-09": 75,
        })
    assert tbl.for_expiry(date(2025, 1, 2)) == 75


def test_lot_anomaly_75_to_25_transition_not_flagged():
    # 75→25 genuine transition (2024-05 regime change) must not be flagged.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        tbl = _lot_table({
            "2024-04-25": 75,
            "2024-05-02": 75,
            "2024-05-09": 25,   # real first-25 — left neighbor 75, right neighbor 25
            "2024-05-16": 25,
        })
    assert tbl.for_expiry(date(2024, 5, 9)) == 25
