"""Catalog of available option-data partitions.

The Data-v3 store is scattered across many ``Data N`` folders, each holding
Hive-style partitions::

    <Data N>/Data-v3/instrument=NIFTY50/OPTIONS/expiry=YYYY-MM-DD/options_data.parquet

The Catalog walks all of them once and maps ``expiry -> parquet path``,
de-duplicating expiries that happen to appear in more than one folder
(first one found wins).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

_PARTITION_GLOB = "*/Data-v3/instrument=NIFTY50/OPTIONS/expiry=*/options_data.parquet"


class Catalog:
    def __init__(self, root) -> None:
        self._root = Path(root)
        self._by_expiry: Dict[str, Path] = {}
        for parquet in sorted(self._root.glob(_PARTITION_GLOB)):
            expiry = parquet.parent.name.split("expiry=", 1)[1]
            self._by_expiry.setdefault(expiry, parquet)

    def expiries(self) -> List[str]:
        return sorted(self._by_expiry)

    def path_for(self, expiry: str) -> Optional[Path]:
        return self._by_expiry.get(expiry)
