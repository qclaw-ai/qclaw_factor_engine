# -*- coding: utf-8 -*-
"""yearly_parquet 辅助函数与合并写盘（无 DB）."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import polars as pl
import pytest

from factor_engine.factor_engine_runner import (
    _iter_calendar_years_in_range,
    _merge_write_yearly_parquet_long,
    _trade_date_end_required_for_year,
    _trade_date_start_required_for_year,
)


def test_iter_calendar_years_single_year() -> None:
    assert _iter_calendar_years_in_range(date(2024, 6, 1), date(2024, 6, 2)) == [2024]


def test_iter_calendar_years_cross_year() -> None:
    assert _iter_calendar_years_in_range(date(2024, 12, 1), date(2025, 1, 15)) == [2024, 2025]


def test_iter_calendar_years_inverted() -> None:
    assert _iter_calendar_years_in_range(date(2025, 1, 1), date(2024, 1, 1)) == []


def test_trade_date_end_required_for_year() -> None:
    assert _trade_date_end_required_for_year(2025, date(2025, 3, 1)) == date(2025, 3, 1)
    assert _trade_date_end_required_for_year(2025, date(2026, 1, 1)) == date(2025, 12, 31)


def test_trade_date_start_required_for_year() -> None:
    assert _trade_date_start_required_for_year(2025, date(2025, 3, 1)) == date(2025, 3, 1)
    assert _trade_date_start_required_for_year(2025, date(2024, 6, 1)) == date(2025, 1, 1)


def test_merge_write_yearly_parquet_roundtrip(tmp_path: Path) -> None:
    root = tmp_path
    df = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
            "stock_code": ["000001.SZ", "000001.SZ"],
            "factor_value": [1.0, 2.0],
        }
    )
    rel, ds, de = _merge_write_yearly_parquet_long(
        project_root=root,
        universe="ZZ500",
        factor_id="FACTOR_TEST_001",
        year=2025,
        df_new=df,
    )
    assert (
        "factor_values_parquet/yearly/by_universe/ZZ500/FACTOR_TEST_001/FACTOR_TEST_001-2025.parquet"
        in rel
    )
    assert ds == date(2025, 1, 2)
    assert de == date(2025, 1, 3)

    # 同键覆盖：后写 3.0 覆盖 2.0
    df2 = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-01-03"]),
            "stock_code": ["000001.SZ"],
            "factor_value": [3.0],
        }
    )
    _merge_write_yearly_parquet_long(
        project_root=root,
        universe="ZZ500",
        factor_id="FACTOR_TEST_001",
        year=2025,
        df_new=df2,
    )

    p = root.joinpath(*rel.split("/"))
    out = pl.read_parquet(str(p))
    jan3 = out.filter(pl.col("trade_date").cast(pl.Utf8).str.slice(0, 10) == "2025-01-03")
    assert jan3.height == 1
    assert float(jan3["factor_value"][0]) == pytest.approx(3.0)


def test_merge_write_yearly_migrates_legacy_flat_file(tmp_path: Path) -> None:
    """旧路径 .../universe/{factor_id}-{year}.parquet 应在首次合并时迁入 factor_id 子目录。"""
    root = tmp_path
    universe_dir = root / "factor_values_parquet" / "yearly" / "by_universe" / "HS300"
    universe_dir.mkdir(parents=True, exist_ok=True)
    legacy = universe_dir / "FACTOR_LEGACY_001-2025.parquet"
    df_old = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-01-02"]),
            "stock_code": ["600000.SH"],
            "factor_value": [9.0],
        }
    )
    pl.from_pandas(df_old).write_parquet(str(legacy), compression="zstd")

    df_new = pd.DataFrame(
        {
            "trade_date": pd.to_datetime(["2025-01-03"]),
            "stock_code": ["600000.SH"],
            "factor_value": [1.0],
        }
    )
    rel, _, _ = _merge_write_yearly_parquet_long(
        project_root=root,
        universe="HS300",
        factor_id="FACTOR_LEGACY_001",
        year=2025,
        df_new=df_new,
    )
    assert (
        "factor_values_parquet/yearly/by_universe/HS300/FACTOR_LEGACY_001/FACTOR_LEGACY_001-2025.parquet"
        in rel
    )
    assert not legacy.is_file()
    p = root.joinpath(*rel.split("/"))
    out = pl.read_parquet(str(p))
    assert out.height == 2
