from pathlib import Path

from engine.data.catalog import Catalog


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
