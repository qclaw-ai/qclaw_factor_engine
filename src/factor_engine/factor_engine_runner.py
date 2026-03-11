#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime
from typing import List

import pandas as pd
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


def _ma(series: pd.Series, window: int) -> pd.Series:
    """截面内按股票分组的简单移动平均"""
    return (
        series.groupby(level="stock_code")
        .rolling(window, min_periods=window)
        .mean()
        .reset_index(level=0, drop=True)
    )


def _ref(series: pd.Series, n: int) -> pd.Series:
    """按股票分组向前平移 n 日"""
    return (
        series.groupby(level="stock_code")
        .shift(n)
    )


def compute_factor_values(formula: str, price_df: pd.DataFrame) -> pd.Series:
    """基于最小 DSL 计算因子值

    支持：
    - 字段：open, high, low, close, volume, turnover
    - 函数：MA(x, n), REF(x, n)
    - 运算：+ - * /
    """
    locals_dict = {
        "open": price_df["open"],
        "high": price_df["high"],
        "low": price_df["low"],
        "close": price_df["close"],
        "volume": price_df["volume"],
        "turnover": price_df["turnover"],
        "MA": _ma,
        "REF": _ref,
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

