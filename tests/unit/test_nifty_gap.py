from datetime import date
import pandas as pd
from analysis.snapshot_nifty_gap import build_gap_table
from engine.equity.nifty_gap import NiftyGapProvider

def test_build_gap_table_computes_daily_gap():
    df = pd.DataFrame({
        "date": pd.to_datetime([
            "2024-01-01 09:15", "2024-01-01 15:29",   # day1 open 100, close 110
            "2024-01-02 09:15", "2024-01-02 15:29",   # day2 open 121, close 130
        ]),
        "open":  [100.0, 108.0, 121.0, 128.0],
        "close": [101.0, 110.0, 122.0, 130.0],
    })
    out = build_gap_table(df)
    # first day dropped (no prior close); day2 gap = 121/110 - 1
    assert list(out["date"].astype(str)) == ["2024-01-02"]
    assert out["ngap"].iloc[0] == 121.0 / 110.0 - 1.0

def test_provider_lookup(tmp_path):
    p = tmp_path / "gap.parquet"
    pd.DataFrame({"date": [date(2024, 1, 2)], "ngap": [0.0123]}).to_parquet(p)
    prov = NiftyGapProvider(store_path=str(p))
    assert prov.gap_for(date(2024, 1, 2)) == 0.0123
    assert prov.gap_for(date(2024, 1, 3)) is None   # missing -> None (not aligned)
