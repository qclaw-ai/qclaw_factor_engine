#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime
from typing import List

import pandas as pd
import numpy as np
from sqlalchemy import text

# 对齐旧项目：把 common / factor_docs 加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors, FactorDefinition

logger = setup_logger("factor_engine_runner", "logs/factor_engine_runner.log")


def _load_stock_daily(
    config_file: str,
    start_date: str,
    end_date: str,
    universe: str | None = None,
) -> pd.DataFrame:
    """从 stock_daily 拉取行情数据，返回 DataFrame

    index: MultiIndex [trade_date, stock_code]
    columns: open, high, low, close, volume, turnover
    """
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        sql = """
        SELECT
            stock_code,
            trade_date,
            open,
            high,
            low,
            close,
            volume,
            turnover
        FROM stock_daily
        WHERE trade_date BETWEEN :start_date AND :end_date
        """
        params = {"start_date": start_date, "end_date": end_date}

        df = pd.read_sql(text(sql), session.bind, params=params)
    finally:
        session.close()

    if df.empty:
        logger.warning(f"stock_daily 在 {start_date} ~ {end_date} 区间无数据")
        return df

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index(["trade_date", "stock_code"]).sort_index()

    return df


def _ma(series: pd.Series, window: int, m: int | None = None) -> pd.Series:
    """截面内按股票分组的简单移动平均

    兼容 alpha191 中 SMA/SMEAN 等 3 参数形式，这里先忽略 m，按简单 MA 处理。
    """
    ma = (
        series.groupby(level="stock_code")
        .rolling(window, min_periods=window)
        .mean()
        .reset_index(level=0, drop=True)
    )
    # 对齐原始索引，避免与原 series 比较时报 "Can only compare identically-labeled Series objects"
    return ma.reindex(series.index)


def _ref(series: pd.Series, n: int) -> pd.Series:
    """按股票分组向前平移 n 日"""
    return (
        series.groupby(level="stock_code")
        .shift(n)
    )


def _log(series: pd.Series) -> pd.Series:
    return np.log(series)


def _delta(x, n: int | None = None) -> pd.Series:
    """时间序列差分，对应 alpha191 的 DELTA(A, n)

    兼容两种调用形式：
    - DELTA(series, n)
    - DELTA((series, n))  # 由于 eval/括号问题可能被打包成 tuple
    """
    # 兼容 tuple 打包形式：DELTA(((expr, n))) 之类
    if n is None:
        if isinstance(x, tuple) and len(x) == 2:
            series, n = x
        else:
            raise TypeError("_delta 期望 (series, n) 或 (series, n) 形式的参数")
    else:
        series = x

    if not isinstance(series, pd.Series):
        raise TypeError("_delta 第一个参数必须是 pandas.Series")

    return series.groupby(level="stock_code").diff(int(n))


def _ts_sum(series: pd.Series, window: int) -> pd.Series:
    """时间序列滚动求和（按股票分组），对应 alpha191 的 TS_SUM"""
    summed = (
        series.groupby(level="stock_code")
        .rolling(window, min_periods=window)
        .sum()
        .reset_index(level=0, drop=True)
    )

    # 保证返回的 index 与原始 series 完全一致，避免后续比较时报
    # "Can only compare identically-labeled Series objects"
    return summed.reindex(series.index)


def _ts_max(series: pd.Series, window: int) -> pd.Series:
    """时间序列滚动最大值（按股票分组），对应 alpha191 的 TS_MAX"""
    maxed = (
        series.groupby(level="stock_code")
        .rolling(window, min_periods=window)
        .max()
        .reset_index(level=0, drop=True)
    )
    # 对齐原始索引，避免后续比较时报 index 不一致
    return maxed.reindex(series.index)


def _ts_min(series: pd.Series, window: int) -> pd.Series:
    """时间序列滚动最小值（按股票分组），对应 alpha191 的 TS_MIN"""
    mined = (
        series.groupby(level="stock_code")
        .rolling(window, min_periods=window)
        .min()
        .reset_index(level=0, drop=True)
    )
    # 对齐原始索引，避免后续比较时报 index 不一致
    return mined.reindex(series.index)


def _ts_rank(series: pd.Series, window: int) -> pd.Series:
    """时间窗口内 rank（按股票分组），对应 alpha191 TS_RANK

    实现：对每个股票 rolling(window)，在窗口内对数值做 rank，
    取窗口最后一个点的相对名次并归一到 0-1。
    """

    def _group_ts_rank(g: pd.Series) -> pd.Series:
        r = (
            g.rolling(window, min_periods=window)
            .apply(
                lambda s: s.rank(method="average").iloc[-1]
                / (len(s) if len(s) > 1 else 1),
                raw=False,
            )
        )
        return r

    out = (
        series.groupby(level="stock_code")
        .apply(_group_ts_rank)
        .reset_index(level=0, drop=True)
    )
    # 对齐原始索引，避免后续组合时报 index 不一致
    return out.reindex(series.index)


def _std(series: pd.Series, window: int | None = None) -> pd.Series:
    """时间序列滚动标准差（按股票分组），对应 alpha191 的 STD/STDDEV

    - STD(A, n)：过去 n 日滚动标准差；
    - STD(A)：若未给窗口，则用 expanding 标准差（从起始到当前）。
    """
    g = series.groupby(level="stock_code")
    if window is None:
        return (
            g.expanding(min_periods=1)
            .std()
            .reset_index(level=0, drop=True)
        )

    return (
        g.rolling(window, min_periods=window)
        .std()
        .reset_index(level=0, drop=True)
    )


def _sum(series: pd.Series, window: int) -> pd.Series:
    """简单封装，方便 SUM(x, n) 写法，语义同 TS_SUM"""
    return _ts_sum(series, window)


def _abs(series: pd.Series) -> pd.Series:
    return series.abs()


def _sign(series: pd.Series) -> pd.Series:
    """符号函数：>0 -> 1, <0 -> -1, ==0 -> 0"""
    return np.sign(series)


def _min(x, y) -> pd.Series:
    """逐点最小值，兼容 Series / 标量组合"""
    if isinstance(x, pd.Series) and isinstance(y, pd.Series):
        a, b = x.align(y)
    elif isinstance(x, pd.Series):
        a = x
        b = pd.Series(y, index=x.index)
    elif isinstance(y, pd.Series):
        a = pd.Series(x, index=y.index)
        b = y
    else:
        # 两个标量，返回标量 Series
        return pd.Series(np.minimum(x, y))

    return pd.concat([a, b], axis=1).min(axis=1)


def _max(x, y) -> pd.Series:
    """逐点最大值，兼容 Series / 标量组合"""
    if isinstance(x, pd.Series) and isinstance(y, pd.Series):
        a, b = x.align(y)
    elif isinstance(x, pd.Series):
        a = x
        b = pd.Series(y, index=x.index)
    elif isinstance(y, pd.Series):
        a = pd.Series(x, index=y.index)
        b = y
    else:
        return pd.Series(np.maximum(x, y))

    return pd.concat([a, b], axis=1).max(axis=1)


def _pow(x: pd.Series, y) -> pd.Series:
    """幂函数，对标 alpha191 的 POW/POWER"""
    return np.power(x, y)


def _scale(series: pd.Series, k: float = 1.0) -> pd.Series:
    """按 alpha191 习惯，把序列缩放到绝对值和为 k（默认 1）"""
    abs_sum = series.abs().groupby(level="trade_date").transform("sum")
    return series / abs_sum * k


def _if(cond, x, y):
    """条件选择，对应 alpha191 里的 IF 或我们转换出来的三目表达式

    注意：保持返回值为 pandas.Series，方便后续继续做 groupby/rolling 等操作。
    """
    # 统一用 numpy.where 计算，然后包装回 Series，彻底避免 BlockManager 的 dtype 拼接问题。
    # 规则：
    # - 优先用 cond 的索引，其次用 x/y 的索引；若都不是 Series，则返回简单一维 Series。

    # 1) 纯标量 / ndarray 场景：不依赖 index，直接返回简单 Series
    if not isinstance(cond, pd.Series) and not isinstance(x, pd.Series) and not isinstance(y, pd.Series):
        return pd.Series(np.where(cond, x, y))

    # 2) 确定目标索引（优先 cond，然后 x，再 y）
    idx = None
    if isinstance(cond, pd.Series):
        idx = cond.index
    if isinstance(x, pd.Series) and idx is None:
        idx = x.index
    if isinstance(y, pd.Series) and idx is None:
        idx = y.index

    if idx is None:
        # 理论上走不到这里，兜底一下
        return pd.Series(np.where(cond, x, y))

    # 3) 构造对齐到 idx 的 ndarray
    if isinstance(cond, pd.Series):
        cond_arr = cond.reindex(idx).to_numpy()
    else:
        cond_arr = np.full(len(idx), bool(cond))

    def _to_arr(v):
        if isinstance(v, pd.Series):
            return v.reindex(idx).to_numpy()
        return np.full(len(idx), v)

    x_arr = _to_arr(x)
    y_arr = _to_arr(y)

    out = np.where(cond_arr, x_arr, y_arr)
    return pd.Series(out, index=idx)


def _rank(series: pd.Series) -> pd.Series:
    # 截面 rank，0-1 归一
    df = series.to_frame("v")

    def _cs_rank(g: pd.DataFrame) -> pd.DataFrame:
        s = g["v"]
        r = s.rank(method="average")
        g["v"] = (r - 1) / (len(r) - 1) if len(r) > 1 else 0.0
        return g

    df = df.groupby(level="trade_date", group_keys=False).apply(_cs_rank)
    return df["v"]

def _corr(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
    df_xy = pd.concat({"x": x, "y": y}, axis=1)

    def _corr_group(g: pd.DataFrame) -> pd.Series:
        return g["x"].rolling(window, min_periods=window).corr(g["y"])

    out = (
        df_xy.groupby(level="stock_code")
        .apply(_corr_group)
        .reset_index(level=0, drop=True)
    )
    # 对齐原始索引，避免比较/组合时报 "Can only compare identically-labeled Series objects"
    return out.reindex(x.index)


def _covariance(x: pd.Series, y: pd.Series, window: int) -> pd.Series:
    """时间序列滚动协方差，对应 alpha191 的 COVIANCE(A, B, n)"""
    df_xy = pd.concat({"x": x, "y": y}, axis=1)

    def _cov_group(g: pd.DataFrame) -> pd.Series:
        return g["x"].rolling(window, min_periods=window).cov(g["y"])

    return (
        df_xy.groupby(level="stock_code")
        .apply(_cov_group)
        .reset_index(level=0, drop=True)
    )


def _prod(series: pd.Series, window: int) -> pd.Series:
    """时间序列滚动累乘，对应 alpha191 的 PROD(A, n)"""
    return (
        series.groupby(level="stock_code")
        .rolling(window, min_periods=window)
        .apply(lambda s: np.prod(s.values), raw=False)
        .reset_index(level=0, drop=True)
    )


def _count(cond: pd.Series, window: int) -> pd.Series:
    """时间序列条件计数：过去 n 日内 condition 为真次数，对应 COUNT(condition, n)"""
    # cond 可能是 bool / 0-1 数值，也可能是纯标量 True/False
    if isinstance(cond, pd.Series):
        s = cond.astype(float)
        out = (
            s.groupby(level="stock_code")
            .rolling(window, min_periods=window)
            .sum()
            .reset_index(level=0, drop=True)
        )
        return out

    # 标量条件：直接返回常数 Series，避免 'int' object has no attribute 'astype'
    val = float(bool(cond))
    return pd.Series(val, index=pd.Index([], name="idx"))


def _regbeta(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """滚动回归系数 beta(A 对 B)，对应 REGBETA(A, B, n)

    实现：对每只股票，按窗口 n 计算：
        beta = cov(A, B) / var(B)
    """
    df_ab = pd.concat({"a": a, "b": b}, axis=1)

    def _beta_group(g: pd.DataFrame) -> pd.Series:
        a_s = g["a"]
        b_s = g["b"]
        cov = a_s.rolling(window, min_periods=window).cov(b_s)
        var = b_s.rolling(window, min_periods=window).var()
        beta = cov / var.replace(0, np.nan)
        return beta

    return (
        df_ab.groupby(level="stock_code")
        .apply(_beta_group)
        .reset_index(level=0, drop=True)
    )


def _regresi(a: pd.Series, b: pd.Series, window: int) -> pd.Series:
    """滚动回归残差 RESI(A 对 B)，对应 REGRESI(A, B, n)

    对每个窗口，先算 beta、alpha，再返回 A_t - alpha - beta * B_t。
    """
    df_ab = pd.concat({"a": a, "b": b}, axis=1)

    def _resid_group(g: pd.DataFrame) -> pd.Series:
        a_s = g["a"]
        b_s = g["b"]
        roll = a_s.rolling(window, min_periods=window)

        mean_a = roll.mean()
        mean_b = b_s.rolling(window, min_periods=window).mean()
        cov = a_s.rolling(window, min_periods=window).cov(b_s)
        var = b_s.rolling(window, min_periods=window).var()
        beta = cov / var.replace(0, np.nan)
        alpha = mean_a - beta * mean_b
        resid = a_s - alpha - beta * b_s
        return resid

    return (
        df_ab.groupby(level="stock_code")
        .apply(_resid_group)
        .reset_index(level=0, drop=True)
    )


def _sumif(a: pd.Series, cond: pd.Series, window: int) -> pd.Series:
    """条件求和，对应 SUMIF(A, n, condition)

    简化实现：对过去 n 日内 cond 为真位置的 A 求和。
    """
    masked = a.where(cond.astype(bool), 0.0)
    return _ts_sum(masked, window)


def _wma(series: pd.Series, window: int) -> pd.Series:
    """加权移动平均，对应 WMA(A, n)，权重 0.9^i（i 为距当前的滞后期）"""

    weights = np.array([0.9 ** i for i in range(window)][::-1], dtype=float)
    weights = weights / weights.sum()

    def _wma_group(s: pd.Series) -> pd.Series:
        def _wma_window(x: pd.Series) -> float:
            # x 长度不足 window 时返回 NaN
            if len(x) < window:
                return np.nan
            return float(np.dot(x.values, weights))

        return (
            s.rolling(window, min_periods=window)
            .apply(_wma_window, raw=False)
        )

    return (
        series.groupby(level="stock_code")
        .apply(_wma_group)
        .reset_index(level=0, drop=True)
    )


def _decaylinear(series: pd.Series, window: int) -> pd.Series:
    """线性衰减加权平均，对应 DECAYLINEAR(A, d)，权重 d, d-1, ..., 1"""

    weights = np.arange(1, window + 1, dtype=float)
    weights = weights / weights.sum()

    def _decay_group(s: pd.Series) -> pd.Series:
        def _decay_window(x: pd.Series) -> float:
            if len(x) < window:
                return np.nan
            return float(np.dot(x.values, weights))

        return (
            s.rolling(window, min_periods=window)
            .apply(_decay_window, raw=False)
        )

    return (
        series.groupby(level="stock_code")
        .apply(_decay_group)
        .reset_index(level=0, drop=True)
    )


def _filter(a: pd.Series, cond: pd.Series) -> pd.Series:
    """简单实现 FILTER(A, condition)：不满足条件的位置置为 NaN"""
    return a.where(cond.astype(bool))


def _highday(series: pd.Series, window: int) -> pd.Series:
    """HIGHDAY(A, n)：过去 n 日内最大值距离当前的间隔"""

    def _highday_group(s: pd.Series) -> pd.Series:
        def _idx(x: pd.Series) -> float:
            if len(x) < window:
                return np.nan
            # 最近一天是位置 window-1
            return float((window - 1) - int(np.argmax(x.values)))

        return (
            s.rolling(window, min_periods=window)
            .apply(_idx, raw=False)
        )

    return (
        series.groupby(level="stock_code")
        .apply(_highday_group)
        .reset_index(level=0, drop=True)
    )


def _lowday(series: pd.Series, window: int) -> pd.Series:
    """LOWDAY(A, n)：过去 n 日内最小值距离当前的间隔"""

    def _lowday_group(s: pd.Series) -> pd.Series:
        def _idx(x: pd.Series) -> float:
            if len(x) < window:
                return np.nan
            return float((window - 1) - int(np.argmin(x.values)))

        return (
            s.rolling(window, min_periods=window)
            .apply(_idx, raw=False)
        )

    return (
        series.groupby(level="stock_code")
        .apply(_lowday_group)
        .reset_index(level=0, drop=True)
    )


def _sumac(series: pd.Series, window: int) -> pd.Series:
    """SUMAC(A, n)：前 n 项累加，这里按股票做 expanding 累加"""
    return (
        series.groupby(level="stock_code")
        .expanding(min_periods=1)
        .sum()
        .reset_index(level=0, drop=True)
    )


def compute_factor_values(formula: str, price_df: pd.DataFrame) -> pd.Series:
    """基于最小 DSL 计算因子值

    支持：
    - 字段：open, high, low, close, volume, turnover
    - 函数：MA(x, n), REF(x, n)
    - 运算：+ - * /
    """
    # 衍生字段：VWAP / RET / 市场因子 / 各类中间变量（DTM / DBM / TR / HD / LD 等）
    vwap = price_df["turnover"] / price_df["volume"]

    open_ = price_df["open"]
    high = price_df["high"]
    low = price_df["low"]
    close = price_df["close"]

    # 日收益率：按股票分组的 close_pct_change（RET）
    ret = close.groupby(level="stock_code").pct_change()

    # 前一日价格：用于 RET / DTM / DBM / TR / HD / LD 等
    prev_open = open_.groupby(level="stock_code").shift(1)
    prev_close = close.groupby(level="stock_code").shift(1)
    prev_high = high.groupby(level="stock_code").shift(1)
    prev_low = low.groupby(level="stock_code").shift(1)

    # DTM: (OPEN<=DELAY(OPEN,1)?0:MAX((HIGH-OPEN),(OPEN-DELAY(OPEN,1))))
    cond_dtm_zero = (open_ <= prev_open) | prev_open.isna()
    dtm_candidate1 = high - open_
    dtm_candidate2 = open_ - prev_open
    dtm_max = pd.concat([dtm_candidate1, dtm_candidate2], axis=1).max(axis=1)
    dtm = pd.Series(0.0, index=price_df.index)
    dtm[~cond_dtm_zero] = dtm_max[~cond_dtm_zero]

    # DBM: (OPEN>=DELAY(OPEN,1)?0:MAX((OPEN-LOW),(OPEN-DELAY(OPEN,1))))
    cond_dbm_zero = (open_ >= prev_open) | prev_open.isna()
    dbm_candidate1 = open_ - low
    dbm_candidate2 = open_ - prev_open
    dbm_max = pd.concat([dbm_candidate1, dbm_candidate2], axis=1).max(axis=1)
    dbm = pd.Series(0.0, index=price_df.index)
    dbm[~cond_dbm_zero] = dbm_max[~cond_dbm_zero]

    # TR: MAX(MAX(HIGH-LOW,ABS(HIGH-DELAY(CLOSE,1))),ABS(LOW-DELAY(CLOSE,1)))
    tr_range1 = high - low
    tr_range2 = (high - prev_close).abs()
    tr_range3 = (low - prev_close).abs()
    tr = pd.concat([tr_range1, tr_range2, tr_range3], axis=1).max(axis=1)

    # HD: HIGH-DELAY(HIGH,1)
    hd = high - prev_high

    # LD: DELAY(LOW,1)-LOW
    ld = prev_low - low

    # 占位的三因子：MKT、SMB、HML（未来可从外部因子表接入真实值）
    zeros_factor = pd.Series(0.0, index=price_df.index)

    # SEQUENCE(n)：按文档含义代表“时间序列 1~n”，这里近似为每只股票自己的交易日序列 1,2,...
    seq_index = price_df.groupby(level="stock_code").cumcount() + 1

    locals_dict = {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": price_df["volume"],
        "turnover": price_df["turnover"],
        "VWAP": vwap,
        "vwap": vwap,
        "RET": ret,
        "ret": ret,
        "MKT": zeros_factor,
        "SMB": zeros_factor,
        "HML": zeros_factor,
        "BANCHMARKINDEXCLOSE": zeros_factor,
        "BANCHMARKINDEXOPEN": zeros_factor,

        # 时间序列辅助：SEQUENCE(n) —— 近似为每只股票的交易日序列
        "SEQUENCE": lambda n, s=seq_index: s,
        # DTM / DBM / TR / HD / LD：Alpha 相关文档里出现的中间量，这里按文档定义真实计算
        "DTM": dtm,
        "DBM": dbm,
        "TR": tr,
        "HD": hd,
        "LD": ld,
        "MA": _ma,
        "REF": _ref,

        # Alpha191 风格算子
        "LOG": _log,
        "DELTA": _delta,
        "RANK": _rank,
        "CORR": _corr,
        "TS_SUM": _ts_sum,
        "TS_MAX": _ts_max,
        "TS_MIN": _ts_min,
        "TS_RANK": _ts_rank,
        "STD": _std,
        "STDDEV": _std,
        "SUM": _sum,
        "ABS": _abs,
        "SIGN": _sign,
        "MIN": _min,
        "MAX": _max,
        "POW": _pow,
        "POWER": _pow,
        "SCALE": _scale,
        "IF": _if,
        "COVIANCE": _covariance,
        "COVARIANCE": _covariance,
        "SMEAN": _ma,
        "PROD": _prod,
        "COUNT": _count,
        "REGBETA": _regbeta,
        "REGRESI": _regresi,
        "SUMIF": _sumif,
        "WMA": _wma,
        "DECAYLINEAR": _decaylinear,
        "FILTER": _filter,
        "HIGHDAY": _highday,
        "LOWDAY": _lowday,
        "SUMAC": _sumac,

        # 大小写都兼容 / 兼顾内部 DSL 与 alpha191 原始大小写
        "log": _log,
        "delta": _delta,
        "rank": _rank,
        "corr": _corr,
        "ts_sum": _ts_sum,
        "ts_max": _ts_max,
        "ts_min": _ts_min,
        "ts_rank": _ts_rank,
        "std": _std,
        "stddev": _std,
        "sum": _sum,
        "abs": _abs,
        "sign": _sign,
        "min": _min,
        "max": _max,
        "pow": _pow,
        "power": _pow,
        "scale": _scale,
        "if": _if,
        "covariance": _covariance,
        "smean": _ma,
        "prod": _prod,
        "count": _count,
        "regbeta": _regbeta,
        "regresi": _regresi,
        "sumif": _sumif,
        "wma": _wma,
        "decaylinear": _decaylinear,
        "filter": _filter,
        "highday": _highday,
        "lowday": _lowday,
        "sumac": _sumac,
        "dtm": dtm,
        "dbm": dbm,
        "tr": tr,
        "hd": hd,
        "ld": ld,
        "banchmarkindexclose": zeros_factor,
        "banchmarkindexopen": zeros_factor,
        "sequence": lambda n, s=seq_index: s,
    }

    # 使用受限 eval，仅提供我们需要的局部变量
    result = eval(formula, {"__builtins__": {}}, locals_dict)  # noqa: S307
    if not isinstance(result, pd.Series):
        raise ValueError("公式计算结果不是 pandas.Series")
    return result


def winsorize_and_standardize(series: pd.Series) -> pd.Series:
    """按文档约定进行去极值 + 标准化

    - 去极值：按当日截面 1%–99% 分位数剪裁
    - 标准化：按当日截面 Z-score
    """
    df = series.to_frame("factor_value").copy()

    # 截面去极值
    def _winsorize(group: pd.DataFrame) -> pd.DataFrame:
        s = group["factor_value"]
        if s.isna().all():
            return group
        q01 = s.quantile(0.01)
        q99 = s.quantile(0.99)
        group["factor_value"] = s.clip(lower=q01, upper=q99)
        return group

    df = df.groupby(level="trade_date", group_keys=False).apply(_winsorize)

    # 截面标准化
    def _zscore(group: pd.DataFrame) -> pd.DataFrame:
        s = group["factor_value"]
        mean = s.mean()
        std = s.std()
        if std == 0 or pd.isna(std):
            group["factor_value"] = 0.0
        else:
            group["factor_value"] = (s - mean) / std
        return group

    df = df.groupby(level="trade_date", group_keys=False).apply(_zscore)

    return df["factor_value"]


def run_factor_engine(config_file: str = "src/factor_engine/config.ini") -> None:
    logger.info("启动 factor_engine_runner")

    cfg = Config(config_file=config_file)

    start_date = cfg.get("factor_engine", "start_date", fallback="2024-01-01")
    end_date = cfg.get(
        "factor_engine",
        "end_date",
        fallback=datetime.now().strftime("%Y-%m-%d"),
    )
    factor_ids_raw = cfg.get("factor_engine", "factor_ids", fallback="").strip()
    factor_ids: List[str] = [
        fid.strip()
        for fid in factor_ids_raw.split(",")
        if fid.strip()
    ]

    # 黑名单：配置里可选的 skip_factor_ids，用逗号分隔
    skip_ids_raw = cfg.get("factor_engine", "skip_factor_ids", fallback="").strip()
    skip_factor_ids: List[str] = [
        fid.strip()
        for fid in skip_ids_raw.split(",")
        if fid.strip()
    ]

    logger.info(
        f"配置 - start_date={start_date}, end_date={end_date}, "
        f"factor_ids={factor_ids or 'ALL'}"
    )

    all_factors = load_all_factors()
    if not all_factors:
        logger.error("未解析到任何因子定义，退出")
        return

    # 过滤需要计算的因子
    if factor_ids:
        factors = [f for f in all_factors if f.factor_id in factor_ids]
        missing = set(factor_ids) - {f.factor_id for f in factors}
        if missing:
            logger.warning(f"以下因子ID在因子文档中未找到: {missing}")
    else:
        factors = all_factors

    if skip_factor_ids:
        before = len(factors)
        factors = [f for f in factors if f.factor_id not in skip_factor_ids]
        skipped = before - len(factors)
        if skipped:
            logger.info(
                f"根据配置 skip_factor_ids 跳过 {skipped} 个因子: {sorted(skip_factor_ids)}"
            )

    logger.info(f"本次将计算 {len(factors)} 个因子")

    # 拉行情数据（先不按股票池过滤，后续可以在 SQL 里根据 universe 做细化）
    price_df = _load_stock_daily(config_file=config_file, start_date=start_date, end_date=end_date)
    if price_df.empty:
        logger.error("行情数据为空，无法计算因子")
        return

    for factor in factors:
        logger.info(f"开始计算因子: {factor.factor_id} - {factor.factor_name}")
        try:
            raw_series = compute_factor_values(factor.formula, price_df)
            processed_series = winsorize_and_standardize(raw_series)
        except Exception as e:
            logger.error(f"计算因子 {factor.factor_id} 失败: {e}")
            continue

        # 简单输出前几行结果用于检查
        df_out = processed_series.to_frame("factor_value").reset_index()
        logger.info(
            f"因子 {factor.factor_id} 样例数据（前5行）:\n"
            f"{df_out.head().to_string(index=False)}"
        )

        # 导出完整结果为 CSV，方便排查与后续使用（统一放在项目根的 factor_values 目录）
        output_dir = "factor_values"
        os.makedirs(output_dir, exist_ok=True)
        csv_name = f"{factor.factor_id}_{start_date}_{end_date}.csv".replace(":", "").replace(" ", "")
        csv_path = os.path.join(output_dir, csv_name)
        df_out.to_csv(csv_path, index=False)
        logger.info(f"因子 {factor.factor_id} 结果已导出到 CSV: {csv_path}")


def main():
    run_factor_engine()


if __name__ == "__main__":
    main()

