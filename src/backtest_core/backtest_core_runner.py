#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from dataclasses import dataclass
from typing import Tuple, List, Optional
import numpy as np
import pandas as pd
from sqlalchemy import text

# 把 common 模块加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger

logger = setup_logger("backtest_core_runner", "logs/backtest_core_runner.log")


@dataclass
class BacktestResult:
    factor_id: str
    backtest_period: str
    horizon: str
    ic_value: float
    ic_ir: float
    sharpe_ratio: float
    max_drawdown: float
    turnover: float
    # 大回测实证域（与因子值是否全市场计算无关；写入 factor_backtest.test_universe）
    test_universe: str = "ALL"


def _load_factor_csv(path: str) -> pd.DataFrame:
    """加载 factor_engine 输出的 CSV，返回 MultiIndex DataFrame"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"因子 CSV 文件不存在: {path}")

    df = pd.read_csv(path)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index(["trade_date", "stock_code"]).sort_index()

    if "factor_value" not in df.columns:
        raise ValueError("因子 CSV 中缺少 factor_value 列")

    return df


def _load_close_series(config_file: str, start_date: str, end_date: str) -> pd.Series:
    """从 stock_daily 加载收盘价 Series，MultiIndex 与因子一致"""
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        sql = """
        SELECT
            stock_code,
            trade_date,
            close
        FROM stock_daily
        WHERE trade_date BETWEEN :start_date AND :end_date
        """
        params = {"start_date": start_date, "end_date": end_date}
        df = pd.read_sql(text(sql), session.bind, params=params)
    finally:
        session.close()

    if df.empty:
        raise ValueError("在指定区间内 stock_daily 无收盘价数据")

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index(["trade_date", "stock_code"]).sort_index()
    return df["close"]


def _compute_forward_return(close: pd.Series, horizon: int) -> pd.Series:
    """计算 horizon 日对数收益：ln(close_{t+h} / close_t)"""
    future_close = close.groupby(level="stock_code").shift(-horizon)
    ret = np.log(future_close / close)
    return ret


def _compute_ic_icir(factor: pd.Series, ret: pd.Series) -> Tuple[float, float]:
    """按日截面计算 Spearman IC 和 IC_IR"""
    df = pd.concat(
        [factor.rename("factor"), ret.rename("ret")],
        axis=1,
    ).dropna()

    if df.empty:
        return np.nan, np.nan

    ic_list = []
    for dt, group in df.groupby(level="trade_date"):
        if group["factor"].nunique() < 2 or group["ret"].nunique() < 2:
            continue
        ic = group["factor"].rank().corr(group["ret"].rank())
        if pd.notna(ic):
            ic_list.append(ic)

    if not ic_list:
        return np.nan, np.nan

    ic_array = np.array(ic_list)
    ic_mean = float(ic_array.mean())
    ic_std = float(ic_array.std(ddof=1)) if len(ic_array) > 1 else np.nan
    ic_ir = float(ic_mean / ic_std) if ic_std and not np.isnan(ic_std) else np.nan

    return ic_mean, ic_ir


def _compute_long_short_returns(
    factor: pd.Series,
    ret: pd.Series,
    n_quantiles: int,
) -> Tuple[pd.Series, pd.Series]:
    """构建多空组合日收益序列

    返回：
    - ls_ret: 多空组合日收益（top_quantile - bottom_quantile）
    - avg_turnover: 简化版换手率（按 top 组合日度持仓变化估算）
    """
    df = pd.concat(
        [factor.rename("factor"), ret.rename("ret")],
        axis=1,
    ).dropna()

    if df.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)

    ls_ret_list = []
    turnover_list = []
    prev_long = None

    for dt, group in df.groupby(level="trade_date"):
        if group["factor"].nunique() < n_quantiles:
            continue

        try:
            group = group.copy()
            group["q"] = pd.qcut(group["factor"], n_quantiles, labels=False, duplicates="drop")
        except ValueError:
            # qcut 失败时跳过当日
            continue

        q_ret = group.groupby("q")["ret"].mean()
        if q_ret.empty:
            continue

        long_ret = q_ret.iloc[-1]
        short_ret = q_ret.iloc[0]
        ls_ret = long_ret - short_ret
        ls_ret_list.append((dt, ls_ret))

        # 简化版换手率：long 组合持仓变动比例
        current_long = set(group[group["q"] == group["q"].max()].index.get_level_values("stock_code"))
        if prev_long is not None and current_long:
            intersect = len(prev_long & current_long)
            turnover = 1 - intersect / len(current_long)
            turnover_list.append((dt, turnover))
        prev_long = current_long

    ls_ret_series = pd.Series(
        data=[v for _, v in ls_ret_list],
        index=[d for d, _ in ls_ret_list],
        name="ls_ret",
    ).sort_index()

    turnover_series = pd.Series(
        data=[v for _, v in turnover_list],
        index=[d for d, _ in turnover_list],
        name="turnover",
    ).sort_index()

    return ls_ret_series, turnover_series


def _compute_sharpe_maxdd(ret_series: pd.Series, horizon: int) -> Tuple[float, float]:
    """计算夏普比率和最大回撤（基于多空组合日收益）"""
    if ret_series.empty:
        return np.nan, np.nan

    mean = ret_series.mean()
    std = ret_series.std(ddof=1)
    # 这里简单按 252 交易日年化；若 horizon != 1，可再调整
    sharpe = float(mean / std * np.sqrt(252)) if std and not np.isnan(std) else np.nan

    cum = (1 + ret_series).cumprod()
    peak = cum.cummax()
    drawdown = (cum / peak) - 1
    max_dd = float(drawdown.min())

    return sharpe, max_dd


def _extract_factor_id_from_csv_name(file_name: str, test_universe: str) -> Optional[str]:
    """
    从 CSV 文件名提取 factor_id（全域统一命名）：
    - <factor_id>_<UNIVERSE>_<start>_<end>.csv
    """
    name_no_ext = os.path.splitext(file_name)[0]
    parts = name_no_ext.split("_")
    if len(parts) < 4:
        return None

    u = (test_universe or "ALL").strip().upper()
    # 分域命名：最后三段是 <UNIVERSE>_<start>_<end>
    if len(parts) >= 4 and parts[-3].upper() == u:
        return "_".join(parts[:-3])
    return None


def _discover_csv_files(factor_output_dir: str, test_universe: str) -> Tuple[List[str], str]:
    """
    发现回测要使用的 CSV 文件（全域统一 by_universe，无兼容回退）。
    返回：(文件名列表, 实际读取目录)
    """
    u = (test_universe or "ALL").strip().upper()
    by_u_dir = os.path.join(factor_output_dir, "by_universe", u)

    if not os.path.isdir(by_u_dir):
        return [], by_u_dir

    files = [f for f in os.listdir(by_u_dir) if f.lower().endswith(".csv")]
    return files, by_u_dir


def run_backtest_for_one(
    config_file: str,
    factor_id: str,
    factor_csv_path: str,
    horizon: int,
    n_quantiles: int,
    test_universe: str = "ALL",
) -> BacktestResult | None:
    logger.info(
        f"开始回测因子: {factor_id}, horizon={horizon}, "
        f"n_quantiles={n_quantiles}, csv={factor_csv_path}"
    )

    factor_df = _load_factor_csv(factor_csv_path)
    factor_series = factor_df["factor_value"]

    # 推导回测时间区间
    start_date = factor_series.index.get_level_values("trade_date").min().strftime("%Y-%m-%d")
    end_date = factor_series.index.get_level_values("trade_date").max().strftime("%Y-%m-%d")
    backtest_period = f"{start_date} 至 {end_date}"

    close_series = _load_close_series(config_file=config_file, start_date=start_date, end_date=end_date)
    ret_series = _compute_forward_return(close_series, horizon=horizon)

    ic_value, ic_ir = _compute_ic_icir(factor_series, ret_series)

    ls_ret_series, turnover_series = _compute_long_short_returns(
        factor_series,
        ret_series,
        n_quantiles=n_quantiles,
    )

    sharpe_ratio, max_drawdown = _compute_sharpe_maxdd(ls_ret_series, horizon=horizon)
    turnover = float(turnover_series.mean()) if not turnover_series.empty else np.nan

    logger.info(
        f"回测结果 - factor_id={factor_id}, IC={ic_value:.4f}, IC_IR={ic_ir:.4f}, "
        f"Sharpe={sharpe_ratio:.4f}, MaxDD={max_drawdown:.4f}, Turnover={turnover:.4f}"
    )

    result = BacktestResult(
        factor_id=factor_id,
        backtest_period=backtest_period,
        horizon=str(horizon) + "d",
        ic_value=ic_value,
        ic_ir=ic_ir,
        sharpe_ratio=sharpe_ratio,
        max_drawdown=max_drawdown,
        turnover=turnover,
        test_universe=test_universe,
    )

    return result


def run_backtest(config_file: str = "src/backtest_core/config.ini") -> List[BacktestResult]:
    logger.info("启动 backtest_core_runner")

    cfg = Config(config_file=config_file)

    horizon = cfg.getint("backtest", "horizon", fallback=5)
    n_quantiles = cfg.getint("backtest", "n_quantiles", fallback=10)
    factor_output_dir = cfg.get("backtest", "factor_output_dir", fallback="factor_values").strip()
    factor_ids_raw = cfg.get("backtest", "factor_ids", fallback="").strip()
    test_universe = (cfg.get("backtest", "test_universe", fallback="ALL") or "ALL").strip()

    include_factor_ids: List[str] = [
        fid.strip() for fid in factor_ids_raw.split(",") if fid.strip()
    ]

    if not os.path.isdir(factor_output_dir):
        logger.error(f"因子输出目录不存在: {factor_output_dir}")
        return []

    # 扫描 CSV：全域统一 by_universe（含 ALL）
    csv_files, actual_dir = _discover_csv_files(
        factor_output_dir=factor_output_dir,
        test_universe=test_universe,
    )
    if not csv_files:
        logger.error(
            f"目录 {actual_dir} 下未找到任何 CSV 文件（不再回退旧扁平 factor_values）"
        )
        return []
    logger.info(f"本次回测读取目录: {actual_dir}")

    results: List[BacktestResult] = []

    for file_name in csv_files:
        factor_id = _extract_factor_id_from_csv_name(
            file_name=file_name,
            test_universe=test_universe,
        )
        if not factor_id:
            logger.warning(f"文件名不符合约定，跳过: {file_name}")
            continue
        if include_factor_ids and factor_id not in include_factor_ids:
            continue

        csv_path = os.path.join(actual_dir, file_name)
        try:
            res = run_backtest_for_one(
                config_file=config_file,
                factor_id=factor_id,
                factor_csv_path=csv_path,
                horizon=horizon,
                n_quantiles=n_quantiles,
                test_universe=test_universe,
            )
            if res is not None:
                results.append(res)
        except Exception as e:
            logger.error(f"回测因子 {factor_id} 失败: {e}")

    logger.info(f"本次共完成 {len(results)} 个因子的回测")
    return results


def main():
    run_backtest()


if __name__ == "__main__":
    main()

