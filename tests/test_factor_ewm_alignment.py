#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import importlib.util
import os
import sys
import unittest

import numpy as np
import pandas as pd

# 允许在仓库根执行测试时直接导入 src 下包
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from factor_engine.factor_engine_runner import compute_factor_values


def _load_others_factor_base_func():
    """动态加载 others/factor_base_func.py，作为对齐基准实现。"""
    mod_path = os.path.join(_ROOT, "others", "factor_base_func.py")
    spec = importlib.util.spec_from_file_location("others_factor_base_func", mod_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {mod_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_mock_price_df(days: int = 40, stocks: int = 3) -> pd.DataFrame:
    """构造可重复的模拟行情面板，索引与实盘一致：MultiIndex(trade_date, stock_code)。"""
    rng = np.random.default_rng(20260430)
    trade_dates = pd.date_range("2026-01-01", periods=days, freq="B")
    stock_codes = [f"{i:06d}.XSHE" for i in range(1, stocks + 1)]
    idx = pd.MultiIndex.from_product(
        [trade_dates, stock_codes],
        names=["trade_date", "stock_code"],
    )

    n = len(idx)
    close = np.maximum(rng.normal(50, 10, size=n), 1.0)
    open_ = np.maximum(close + rng.normal(0, 1, size=n), 1.0)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1, size=n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1, size=n))
    low = np.maximum(low, 0.1)
    volume = rng.integers(10_000, 2_000_000, size=n).astype(float)
    turnover = close * volume

    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "turnover": turnover,
        },
        index=idx,
    ).sort_index()


def _calc_ref_by_group(series: pd.Series, func_name: str, *args) -> pd.Series:
    """按 stock_code 分组调用 others 基准函数，输出回 MultiIndex Series。"""
    mod = _load_others_factor_base_func()
    func = getattr(mod, func_name)
    out = (
        series.groupby(level="stock_code")
        .apply(lambda s: func(s, *args))
        .reset_index(level=0, drop=True)
    )
    return out.reindex(series.index)


class TestFactorEwmAlignment(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.price_df = _build_mock_price_df()

    def _assert_same(self, left: pd.Series, right: pd.Series) -> None:
        self.assertTrue(left.index.equals(right.index))
        self.assertTrue(
            np.allclose(
                left.to_numpy(dtype=float),
                right.to_numpy(dtype=float),
                equal_nan=True,
                atol=1e-12,
                rtol=1e-12,
            )
        )

    def test_sma_equals_others(self) -> None:
        dsl_out = compute_factor_values("SMA(close, 5, 2)", self.price_df)
        ref_out = _calc_ref_by_group(self.price_df["close"], "SMA", 5, 2)
        self._assert_same(dsl_out, ref_out)

    def test_wma_equals_others(self) -> None:
        dsl_out = compute_factor_values("WMA(close, 5)", self.price_df)
        ref_out = _calc_ref_by_group(self.price_df["close"], "WMA", 5)
        self._assert_same(dsl_out, ref_out)

    def test_hma_equals_others(self) -> None:
        dsl_out = compute_factor_values("HMA(close, 5)", self.price_df)
        ref_out = _calc_ref_by_group(self.price_df["close"], "HMA", 5)
        self._assert_same(dsl_out, ref_out)

    def test_ema_equals_others(self) -> None:
        dsl_out = compute_factor_values("EMA(close, 5)", self.price_df)
        ref_out = _calc_ref_by_group(self.price_df["close"], "EMA", 5)
        self._assert_same(dsl_out, ref_out)


if __name__ == "__main__":
    unittest.main()
