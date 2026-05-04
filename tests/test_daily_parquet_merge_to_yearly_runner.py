# -*- coding: utf-8 -*-
"""daily_parquet_merge_to_yearly_runner：目录扫描等无 DB 逻辑."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl

from daily_factor_values.daily_parquet_merge_to_yearly_runner import _discover_bundle_trade_dates


def test_discover_bundle_trade_dates_respects_range(tmp_path: Path) -> None:
    root = tmp_path
    u = "HS300"
    base = root / "factor_values_parquet" / "daily" / "by_universe" / u
    (base / "2025-01-02").mkdir(parents=True)
    pl.DataFrame(
        {
            "trade_date": [date(2025, 1, 2)],
            "stock_code": ["1"],
            "factor_id": ["F1"],
            "factor_value": [1.0],
        }
    ).write_parquet(str(base / "2025-01-02" / "factors.parquet"), compression="zstd")
    (base / "2025-01-03").mkdir(parents=True)
    # 无 factors.parquet，不应出现在结果中
    (base / "2025-06-01").mkdir(parents=True)
    pl.DataFrame(
        {
            "trade_date": [date(2025, 6, 1)],
            "stock_code": ["1"],
            "factor_id": ["F1"],
            "factor_value": [1.0],
        }
    ).write_parquet(str(base / "2025-06-01" / "factors.parquet"), compression="zstd")

    found = _discover_bundle_trade_dates(root, u, date(2025, 1, 1), date(2025, 1, 31))
    assert found == [date(2025, 1, 2)]

    found2 = _discover_bundle_trade_dates(root, u, date(2025, 1, 1), date(2025, 12, 31))
    assert found2 == [date(2025, 1, 2), date(2025, 6, 1)]
