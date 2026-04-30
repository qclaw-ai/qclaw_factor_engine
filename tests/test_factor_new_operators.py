#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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

from factor_engine.factor_dsl_allowlist import validate_factor_dsl_formula
from factor_engine.factor_engine_runner import compute_factor_values


def _build_mock_price_df(days: int = 60, stocks: int = 3) -> pd.DataFrame:
    """构造可重复的模拟行情面板，索引与实盘一致：MultiIndex(trade_date, stock_code)。"""
    rng = np.random.default_rng(20260430)
    trade_dates = pd.date_range("2026-01-01", periods=days, freq="B")
    stock_codes = [f"{i:06d}.XSHE" for i in range(1, stocks + 1)]
    idx = pd.MultiIndex.from_product(
        [trade_dates, stock_codes],
        names=["trade_date", "stock_code"],
    )

    n = len(idx)
    base = rng.normal(50, 10, size=n)
    close = np.maximum(base, 1.0)
    open_ = np.maximum(close + rng.normal(0, 1, size=n), 1.0)
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1, size=n))
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1, size=n))
    low = np.maximum(low, 0.1)
    volume = rng.integers(10_000, 2_000_000, size=n).astype(float)
    turnover = close * volume

    df = pd.DataFrame(
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
    return df


class TestFactorNewOperators(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.price_df = _build_mock_price_df()

    def _assert_formula_runs(self, formula: str) -> None:
        """统一断言：白名单可识别、公式可计算、输出为 Series 且索引对齐。"""
        validate_factor_dsl_formula(formula)
        out = compute_factor_values(formula, self.price_df)
        self.assertIsInstance(out, pd.Series)
        self.assertTrue(out.index.equals(self.price_df.index))
        self.assertGreater(len(out), 0)

    def _assert_formula_runs_with_values(self, formula: str) -> None:
        """断言公式可识别、可计算，且结果包含至少一个有效值（非 NaN）。"""
        validate_factor_dsl_formula(formula)
        out = compute_factor_values(formula, self.price_df)
        self.assertIsInstance(out, pd.Series)
        self.assertTrue(out.index.equals(self.price_df.index))
        self.assertGreater(len(out), 0)
        self.assertTrue(out.notna().any())

    def test_new_uppercase_operator_formulas(self) -> None:
        formulas = [
            "DELAY(close, 1)",
            "TS_CORR(close, volume, 5)",
            "TS_STD(close, 5)",
            "TS_MEAN(close, 5)",
            "TS_ARGMAX(high, 5)",
            "TS_ARGMIN(low, 5)",
            "SIGNEDPOWER(close, 3)",
            "TWO_CONDITION(close > open, close, open)",
            "THREE_CONDITION(close > open, close, close < open, open, low)",
            "FOUR_CONDITION(close > open, close, close < open, open, volume > 100000, high, low)",
            "TS_SUMIF(close, close > open, 5)",
            "TS_COV(close, volume, 5)",
            "TS_RANK_PERCENT(close, 5)",
            "TS_PROD(close, 5)",
            "TS_COUNT(close > open, 5)",
            "TS_REGBETA(close, volume, 5)",
            "TS_KURT(close, 5)",
            "TS_SKEW(close, 5)",
            "TS_VAR(close, 5)",
            "SMA(close, 5, 2)",
            "EMA(close, 5)",
            "HMA(close, 5)",
            "TS_MEDIAN(close, 5)",
            "TS_PERCENT(close, 5)",
            "PCT_CHANGE(close, 1)",
            "DECAY_LINEAR(close, 5)",
        ]

        for formula in formulas:
            with self.subTest(formula=formula):
                self._assert_formula_runs(formula)

    def test_new_lowercase_alias_formulas(self) -> None:
        formulas = [
            "returns",
            "delay(close, 1)",
            "ts_corr(close, volume, 5)",
            "ts_argmax(high, 5)",
            "ts_argmin(low, 5)",
            "four_condition(close > open, close, close < open, open, volume > 100000, high, low)",
        ]

        for formula in formulas:
            with self.subTest(formula=formula):
                self._assert_formula_runs(formula)

    def test_md_reference_dsl_formulas(self) -> None:
        """验证 others/md算子参考 中 4 条 DSL 可识别且可计算。"""
        formulas = [
            "FOUR_CONDITION((((TS_SUM(close, 8) / 8) + TS_STD(close, 8)) < (TS_SUM(close, 2) / 2)), (-1 * 1), ((TS_SUM(close, 2) / 2) < ((TS_SUM(close, 8) / 8) - TS_STD(close, 8))), 1, (1 <= (volume / TS_MEAN(volume,20))), 1, (-1 * 1))",
            "SMA(((high+low)/2-(DELAY(high,1)+DELAY(low,1))/2)*(high-low)/volume,7,2)",
            "0.5-RANK(TS_ARGMAX(SIGNEDPOWER(TWO_CONDITION((returns<0), TS_STD(returns,20), close), 2), 5))",
            "FOUR_CONDITION((((TS_SUM(close, 8) / 8) + TS_STD(close, 8)) < (TS_SUM(close, 2) / 2)), (-1 * 1), ((TS_SUM(close,2) / 2) < ((TS_SUM(close, 8) / 8) - TS_STD(close, 8))), 1, ((1 < (volume / TS_MEAN(volume, 20))) | ((volume / TS_MEAN(volume, 20)) == 1)), 1, (-1 * 1))",
        ]

        for formula in formulas:
            with self.subTest(formula=formula):
                self._assert_formula_runs_with_values(formula)


if __name__ == "__main__":
    unittest.main()
