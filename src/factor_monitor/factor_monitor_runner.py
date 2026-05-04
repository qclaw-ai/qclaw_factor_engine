#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import time
from typing import Dict, List

import numpy as np
import pandas as pd
from sqlalchemy import text

# 对齐项目导入风格
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors
from factor_engine.factor_engine_runner import (
    _load_stock_daily,
    compute_factor_values,
    winsorize_and_standardize,
)

logger = setup_logger(
    "factor_monitor_runner",
    "logs/factor_monitor_runner.log",
)


@dataclass
class Thresholds:
    """月监阈值载体。

    - ic_mean_min / ic_ir_min / coverage_min：用于 PASS/FAIL 判定
    - source/version：记录阈值来源，便于审计与回溯
    """
    ic_mean_min: float
    ic_ir_min: float
    coverage_min: float
    source: str
    version: str


def _normalize_universe_code(universe: str | None) -> str:
    """统一股票池编码。

    兼容历史 `ALL_A -> ALL`，并对空值回退到 `ALL`。
    """
    u = (universe or "").strip().upper()
    if not u:
        return "ALL"
    if u == "ALL_A":
        return "ALL"
    return u


def _load_thresholds(
    session,
    *,
    scene: str,
    fallback_ic_mean_min: float,
    fallback_ic_ir_min: float,
    fallback_coverage_min: float,
) -> Thresholds:
    """从 `factor_threshold_config` 读取监控阈值，读不到则回退默认值。

    参数:
    - scene: 监控场景（已约定 `factor_monthly_monitor_ZZ500`）
    - fallback_*: 文档侧兜底阈值（DB 无配置时使用）
    """
    sql = text(
        """
        SELECT version, ic_min, ic_ir_min
        FROM factor_threshold_config
        WHERE scene = :scene
          AND is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"scene": scene}).mappings().first()
    if row:
        return Thresholds(
            ic_mean_min=float(row["ic_min"]) if row["ic_min"] is not None else fallback_ic_mean_min,
            ic_ir_min=float(row["ic_ir_min"]) if row["ic_ir_min"] is not None else fallback_ic_ir_min,
            coverage_min=float(fallback_coverage_min),
            source="factor_threshold_config",
            version=str(row["version"] or ""),
        )

    return Thresholds(
        ic_mean_min=float(fallback_ic_mean_min),
        ic_ir_min=float(fallback_ic_ir_min),
        coverage_min=float(fallback_coverage_min),
        source="fallback_doc_default",
        version="",
    )


def _load_candidate_factor_ids(
    session,
    *,
    universe: str,
    max_factors: int,
) -> List[str]:
    """读取当前 universe 下可监控因子列表。

    规则:
    - 来自 `factor_universe_status`
    - 仅 `is_valid = TRUE`
    - `max_factors > 0` 时做数量截断（便于联调）
    """
    sql = text(
        """
        SELECT factor_id
        FROM factor_universe_status
        WHERE test_universe = :universe
          AND is_valid = TRUE
        ORDER BY factor_id
        """
    )
    rows = session.execute(sql, {"universe": universe}).fetchall()
    ids = [str(r[0]).strip() for r in rows if r[0] and str(r[0]).strip()]
    if max_factors > 0:
        ids = ids[:max_factors]
    return ids


def _build_monitor_trading_calendar(
    trade_dates: pd.Index,
    *,
    as_of_date: str,
    monitor_window_days: int,
    warmup_days: int,
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """构建月监时间框架（锚点/监控起点/预热起点）。

    返回:
    - anchor: 实际锚点交易日（as_of 非交易日会向前对齐）
    - monitor_start: 监控窗口起点（长度 `monitor_window_days`）
    - warmup_start: 预热窗口起点（监控起点前再取 warmup）
    """
    as_of_ts = pd.Timestamp(as_of_date)
    dates = pd.DatetimeIndex(pd.unique(pd.to_datetime(trade_dates))).sort_values()
    dates = dates[dates <= as_of_ts]
    if len(dates) == 0:
        raise RuntimeError(f"行情中无 <= as_of_date={as_of_date} 的交易日")

    anchor = pd.Timestamp(dates[-1])
    if len(dates) < monitor_window_days:
        raise RuntimeError(
            f"交易日不足 monitor_window_days={monitor_window_days}，仅有 {len(dates)} 天"
        )

    monitor_dates = dates[-monitor_window_days:]
    monitor_start = pd.Timestamp(monitor_dates[0])

    all_dates = pd.DatetimeIndex(pd.unique(pd.to_datetime(trade_dates))).sort_values()
    warmup_cutoff = all_dates[all_dates < monitor_start]
    if len(warmup_cutoff) >= warmup_days:
        warmup_start = pd.Timestamp(warmup_cutoff[-warmup_days])
    elif len(warmup_cutoff) > 0:
        warmup_start = pd.Timestamp(warmup_cutoff[0])
    else:
        warmup_start = monitor_start

    return anchor, monitor_start, warmup_start


def _compute_forward_log_return(close_series: pd.Series, horizon_days: int) -> pd.Series:
    """计算前向对数收益（与 `label_ic_convention` 选项 A 对齐）。"""
    future_close = close_series.groupby(level="stock_code").shift(-horizon_days)
    ret = np.log(future_close / close_series)
    return ret.rename("fwd_ret")


def _compute_daily_ic_metrics(
    panel_df: pd.DataFrame,
    *,
    factor_id: str,
    monitor_start: pd.Timestamp,
    monitor_end: pd.Timestamp,
) -> dict:
    """按日截面计算单因子月监核心指标。

    指标:
    - ic_mean / ic_ir
    - coverage_mean / missing_rate_mean
    """
    win = panel_df[
        (panel_df["trade_date"] >= monitor_start)
        & (panel_df["trade_date"] <= monitor_end)
    ].copy()
    if win.empty:
        return {
            "factor_id": factor_id,
            "ic_mean": None,
            "ic_ir": None,
            "coverage_mean": 0.0,
            "missing_rate_mean": 1.0,
            "status": "FAIL",
            "reasons": ["empty_monitor_window"],
        }

    ic_by_day: List[float] = []
    coverage_by_day: List[float] = []
    for _, g in win.groupby("trade_date"):
        total = int(len(g))
        valid_mask = g["factor_value"].notna() & g["fwd_ret"].notna()
        valid = int(valid_mask.sum())
        coverage = float(valid / total) if total > 0 else 0.0
        coverage_by_day.append(coverage)
        if valid >= 5:
            ic_val = g.loc[valid_mask, "factor_value"].corr(g.loc[valid_mask, "fwd_ret"])
            if pd.notna(ic_val):
                ic_by_day.append(float(ic_val))

    ic_mean = float(np.mean(ic_by_day)) if ic_by_day else None
    ic_std = float(np.std(ic_by_day, ddof=1)) if len(ic_by_day) >= 2 else None
    ic_ir = None
    if ic_mean is not None and ic_std is not None and ic_std > 0:
        ic_ir = float(ic_mean / ic_std)

    coverage_mean = float(np.mean(coverage_by_day)) if coverage_by_day else 0.0
    missing_rate_mean = float(1.0 - coverage_mean)
    return {
        "factor_id": factor_id,
        "ic_mean": ic_mean,
        "ic_ir": ic_ir,
        "coverage_mean": coverage_mean,
        "missing_rate_mean": missing_rate_mean,
        "status": "PASS",
        "reasons": [],
    }


def _judge_status(rec: dict, th: Thresholds) -> dict:
    """按阈值给单因子记录打 PASS/FAIL，并写入 reasons。"""
    reasons: List[str] = []
    status = "PASS"
    ic_mean = rec.get("ic_mean")
    ic_ir = rec.get("ic_ir")
    cov = rec.get("coverage_mean")

    if ic_mean is None or ic_mean < th.ic_mean_min:
        reasons.append(f"ic_mean<{th.ic_mean_min}")
    if ic_ir is None or ic_ir < th.ic_ir_min:
        reasons.append(f"ic_ir<{th.ic_ir_min}")
    if cov is None or cov < th.coverage_min:
        reasons.append(f"coverage<{th.coverage_min}")

    if reasons:
        status = "FAIL"
    rec["status"] = status
    rec["reasons"] = reasons
    return rec


def _load_daily_parquet_bundle_slice_for_parity(
    factor_root: Path,
    *,
    universe: str,
    trade_date: str,
    factor_id: str,
) -> pd.DataFrame:
    """从日更 bundle ``factors.parquet`` 读取单因子截面（用于生产一致性对账）。"""
    p = (
        factor_root
        / "factor_values_parquet"
        / "daily"
        / "by_universe"
        / universe
        / trade_date
        / "factors.parquet"
    )
    if not p.exists():
        raise FileNotFoundError(str(p))

    df = pd.read_parquet(p)
    cols = set(df.columns)

    if "factor_id" not in cols:
        raise ValueError(f"daily bundle 缺列 factor_id: {p}")

    if "trade_date" not in cols and "date" in cols:
        df = df.rename(columns={"date": "trade_date"})

    required = {"trade_date", "stock_code", "factor_value"}
    miss = sorted(required - set(df.columns))

    if miss:
        raise ValueError(f"daily bundle 缺列 {miss}: {p}")

    out = df.loc[df["factor_id"].astype(str) == str(factor_id), ["trade_date", "stock_code", "factor_value"]].copy()

    if out.empty:
        raise ValueError(f"daily bundle 中无 factor_id={factor_id}: {p}")

    return out


def _build_parity_check(
    factor_root: Path,
    factor_result_by_id: Dict[str, pd.DataFrame],
    monitor_dates: List[str],
    *,
    universe: str,
    sample_factor_count: int,
    sample_day_count: int,
    tolerance: float,
    random_seed: int,
) -> dict:
    """执行日更 ``factors.parquet`` bundle 抽检对账。

    做法:
    - 随机采样若干因子与日期
    - 对比 DB 自算与生产 bundle 中的 ``factor_value``
    - 输出误差分位、匹配率及缺失/错误列表
    """
    rng = random.Random(random_seed)
    factor_ids = sorted(factor_result_by_id.keys())
    sample_factor_ids = factor_ids[:]
    rng.shuffle(sample_factor_ids)
    sample_factor_ids = sample_factor_ids[: max(1, min(sample_factor_count, len(sample_factor_ids)))]

    sample_days = monitor_dates[:]
    rng.shuffle(sample_days)
    sample_days = sample_days[: max(1, min(sample_day_count, len(sample_days)))]

    abs_diffs: List[float] = []
    checked = 0
    missing_files: List[str] = []
    errors: List[str] = []

    for fid in sample_factor_ids:
        base_df = factor_result_by_id[fid]
        for d in sample_days:
            checked += 1
            try:
                prod_df = _load_daily_parquet_bundle_slice_for_parity(
                    factor_root,
                    universe=universe,
                    trade_date=d,
                    factor_id=fid,
                )
            except FileNotFoundError as e:
                missing_files.append(str(e))
                continue
            except Exception as e:
                errors.append(str(e))
                continue

            lhs = base_df[base_df["trade_date"] == d][["stock_code", "factor_value"]].copy()
            rhs = prod_df[["stock_code", "factor_value"]].copy()
            rhs = rhs.rename(columns={"factor_value": "factor_value_prod"})
            merged = lhs.merge(rhs, on="stock_code", how="inner")
            if merged.empty:
                continue
            diff = (merged["factor_value"] - merged["factor_value_prod"]).abs().to_numpy()
            if len(diff) > 0:
                abs_diffs.extend(diff.tolist())

    if abs_diffs:
        abs_arr = np.array(abs_diffs, dtype=float)
        p95 = float(np.percentile(abs_arr, 95))
        p99 = float(np.percentile(abs_arr, 99))
        match_ratio = float(np.mean(abs_arr <= tolerance))
    else:
        p95 = None
        p99 = None
        match_ratio = None

    status = "OK"
    if errors:
        status = "FAIL"
    elif match_ratio is not None and match_ratio < 0.95:
        status = "FAIL"
    elif missing_files:
        status = "WARN"

    return {
        "status": status,
        "sample_factor_ids": sample_factor_ids,
        "sample_dates": sample_days,
        "checked_pairs": checked,
        "abs_diff_p95": p95,
        "abs_diff_p99": p99,
        "match_ratio": match_ratio,
        "tolerance": tolerance,
        "missing_files": missing_files,
        "errors": errors,
    }


def _render_markdown_report(payload: dict) -> str:
    """把 JSON 结果渲染为便于人工查看的 Markdown 报告。"""
    lines: List[str] = []
    lines.append(f"# 因子月监报告 {payload['as_of_date']} ({payload['universe']})")
    lines.append("")
    lines.append("## 运行元数据")
    lines.append("")
    lines.append(f"- 阈值来源: `{payload['thresholds']['source']}`")
    lines.append(f"- 阈值版本: `{payload['thresholds'].get('version', '')}`")
    lines.append(
        f"- 窗口: `{payload['window']['start_date']} ~ {payload['window']['end_date']}`"
    )
    lines.append(f"- warmup_trading_days: `{payload['warmup_trading_days']}`")
    lines.append("")
    lines.append("## 因子结果")
    lines.append("")
    lines.append("| factor_id | ic_mean | ic_ir | coverage | missing | status |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for rec in payload["factors"]:
        ic_mean_str = "" if rec["ic_mean"] is None else f"{rec['ic_mean']:.6f}"
        ic_ir_str = "" if rec["ic_ir"] is None else f"{rec['ic_ir']:.6f}"
        lines.append(
            f"| {rec['factor_id']} | "
            f"{ic_mean_str} | "
            f"{ic_ir_str} | "
            f"{rec['coverage_mean']:.4f} | {rec['missing_rate_mean']:.4f} | {rec['status']} |"
        )
    lines.append("")
    lines.append("## 生产一致性对账")
    lines.append("")
    parity = payload["prod_parity_check"]
    lines.append(f"- 状态: `{parity['status']}`")
    lines.append(f"- 抽检组合数: `{parity['checked_pairs']}`")
    lines.append(f"- match_ratio: `{parity['match_ratio']}`")
    lines.append(f"- abs_diff_p95: `{parity['abs_diff_p95']}`")
    lines.append(f"- abs_diff_p99: `{parity['abs_diff_p99']}`")
    if parity["missing_files"]:
        lines.append("- 缺失文件:")
        for s in parity["missing_files"][:20]:
            lines.append(f"  - `{s}`")
    if parity["errors"]:
        lines.append("- 对账错误:")
        for s in parity["errors"][:20]:
            lines.append(f"  - `{s}`")
    lines.append("")
    return "\n".join(lines)


def run_factor_monitor(config_file: str, as_of_date: str | None = None) -> dict:
    """月监主流程入口。

    流程:
    1) 读配置/阈值/候选因子
    2) 加载行情并计算监控窗口 + 预热窗口
    3) 逐因子自算 + 指标统计 + 阈值判定
    4) 抽检日更 ``factors.parquet`` bundle 做生产一致性对账
    5) 输出 JSON/MD 报告
    """
    total_t0 = time.perf_counter()
    logger.info("月监启动 config_file=%s as_of_date=%s", config_file, as_of_date or "(默认今天)")

    cfg = Config(config_file=config_file)
    universe = _normalize_universe_code(cfg.get("monitor", "universe", fallback="ZZ500"))
    horizon_days = cfg.getint("monitor", "horizon_days", fallback=5)
    monitor_window_days = cfg.getint("monitor", "monitor_window_trading_days", fallback=60)
    warmup_days = cfg.getint("monitor", "warmup_trading_days", fallback=200)
    scene = cfg.get("monitor", "threshold_scene", fallback="factor_monthly_monitor_ZZ500")
    max_factors = cfg.getint("monitor", "max_factors", fallback=0)
    parity_sample_factors = cfg.getint("monitor", "parity_sample_factors", fallback=5)
    parity_sample_days = cfg.getint("monitor", "parity_sample_days", fallback=5)
    parity_tolerance = cfg.getfloat("monitor", "parity_tolerance", fallback=1e-6)
    random_seed = cfg.getint("monitor", "random_seed", fallback=42)

    fallback_cov = cfg.getfloat("monitor", "coverage_min_fallback", fallback=0.8)
    fallback_ic_mean = cfg.getfloat("monitor", "ic_mean_min_fallback", fallback=0.0)
    fallback_ic_ir = cfg.getfloat("monitor", "ic_ir_min_fallback", fallback=0.0)

    docs_cfg = cfg.get("factor_docs", "config_file", fallback=config_file)
    factor_engine_cfg_file = cfg.get("factor_engine", "config_file", fallback=config_file)

    if not as_of_date:
        as_of_date = datetime.now().strftime("%Y-%m-%d")
    logger.info(
        "月监配置 universe=%s horizon_days=%s monitor_window_days=%s warmup_days=%s scene=%s "
        "max_factors=%s parity_sample_factors=%s parity_sample_days=%s parity_tolerance=%s",
        universe,
        horizon_days,
        monitor_window_days,
        warmup_days,
        scene,
        max_factors,
        parity_sample_factors,
        parity_sample_days,
        parity_tolerance,
    )

    t0 = time.perf_counter()
    db = get_db_manager(config_file=config_file)
    session = db.get_session()
    try:
        thresholds = _load_thresholds(
            session,
            scene=scene,
            fallback_ic_mean_min=fallback_ic_mean,
            fallback_ic_ir_min=fallback_ic_ir,
            fallback_coverage_min=fallback_cov,
        )
        candidate_ids = _load_candidate_factor_ids(
            session,
            universe=universe,
            max_factors=max_factors,
        )
    finally:
        session.close()
    logger.info(
        "阶段完成: 阈值与候选因子读取 用时=%.2fs 候选因子数=%d 阈值来源=%s 版本=%s",
        time.perf_counter() - t0,
        len(candidate_ids),
        thresholds.source,
        thresholds.version or "(空)",
    )

    if not candidate_ids:
        raise RuntimeError(f"无候选因子：universe={universe}")

    t0 = time.perf_counter()
    all_defs = load_all_factors(config_file=docs_cfg)
    defs_by_id = {x.factor_id: x for x in all_defs}
    factor_ids = [fid for fid in candidate_ids if fid in defs_by_id]
    logger.info(
        "阶段完成: 因子文档解析 用时=%.2fs docs因子数=%d 候选交集数=%d",
        time.perf_counter() - t0,
        len(all_defs),
        len(factor_ids),
    )
    if not factor_ids:
        raise RuntimeError("候选因子与 factor_docs 无交集")

    t0 = time.perf_counter()
    start_rough = (pd.Timestamp(as_of_date) - pd.Timedelta(days=1200)).strftime("%Y-%m-%d")
    # 显式传入 factor_engine 配置，让 _load_stock_daily 启用 SQL 分域过滤（非 ALL 场景）。
    fe_cfg = Config(config_file=factor_engine_cfg_file)
    logger.info(
        "使用 factor_engine 配置执行行情加载 factor_engine_config=%s universe=%s",
        factor_engine_cfg_file,
        universe,
    )
    price_df = _load_stock_daily(
        config_file=factor_engine_cfg_file,
        start_date=start_rough,
        end_date=as_of_date,
        cfg=fe_cfg,
        universe=universe,
    )
    if price_df.empty:
        raise RuntimeError("stock_daily 为空，无法执行月监")
    logger.info(
        "阶段完成: 行情加载 用时=%.2fs start_rough=%s end=%s 行数=%d",
        time.perf_counter() - t0,
        start_rough,
        as_of_date,
        len(price_df),
    )

    t0 = time.perf_counter()
    anchor, monitor_start, warmup_start = _build_monitor_trading_calendar(
        price_df.index.get_level_values("trade_date"),
        as_of_date=as_of_date,
        monitor_window_days=monitor_window_days,
        warmup_days=warmup_days,
    )
    logger.info(
        "monitor window=%s~%s, warmup_start=%s, anchor=%s",
        monitor_start.strftime("%Y-%m-%d"),
        anchor.strftime("%Y-%m-%d"),
        warmup_start.strftime("%Y-%m-%d"),
        anchor.strftime("%Y-%m-%d"),
    )
    logger.info("阶段完成: 交易日历窗口构建 用时=%.2fs", time.perf_counter() - t0)

    t0 = time.perf_counter()
    td = price_df.index.get_level_values("trade_date")
    price_df = price_df[(td >= warmup_start) & (td <= anchor)]
    close_series = price_df["close"]
    fwd_ret = _compute_forward_log_return(close_series, horizon_days=horizon_days)
    logger.info(
        "阶段完成: 预热切窗+标签计算 用时=%.2fs 切窗后行数=%d",
        time.perf_counter() - t0,
        len(price_df),
    )

    factor_records: List[dict] = []
    factor_panel_for_parity: Dict[str, pd.DataFrame] = {}
    factor_loop_t0 = time.perf_counter()
    factor_total = len(factor_ids)
    logger.info("进入逐因子计算: total=%d", factor_total)
    for idx, fid in enumerate(factor_ids, start=1):
        one_t0 = time.perf_counter()
        fd = defs_by_id[fid]
        logger.info("[%d/%d] 开始因子计算 factor_id=%s", idx, factor_total, fid)
        try:
            raw = compute_factor_values(fd.formula, price_df)
            fac = winsorize_and_standardize(raw)
            fac_df = fac.to_frame("factor_value").reset_index()
            fac_df["trade_date"] = pd.to_datetime(fac_df["trade_date"])

            ret_df = fwd_ret.to_frame("fwd_ret").reset_index()
            ret_df["trade_date"] = pd.to_datetime(ret_df["trade_date"])
            panel = fac_df.merge(ret_df, on=["trade_date", "stock_code"], how="left")
            rec = _compute_daily_ic_metrics(
                panel,
                factor_id=fid,
                monitor_start=monitor_start,
                monitor_end=anchor,
            )
            rec = _judge_status(rec, thresholds)
            factor_records.append(rec)

            p = panel[["trade_date", "stock_code", "factor_value"]].copy()
            p["trade_date"] = p["trade_date"].dt.strftime("%Y-%m-%d")
            factor_panel_for_parity[fid] = p
            logger.info(
                "[%d/%d] 因子完成 factor_id=%s status=%s 用时=%.2fs",
                idx,
                factor_total,
                fid,
                rec["status"],
                time.perf_counter() - one_t0,
            )
        except Exception as e:
            factor_records.append(
                {
                    "factor_id": fid,
                    "ic_mean": None,
                    "ic_ir": None,
                    "coverage_mean": 0.0,
                    "missing_rate_mean": 1.0,
                    "status": "FAIL",
                    "reasons": [f"compute_error:{e}"],
                }
            )
            logger.error(
                "[%d/%d] 因子失败 factor_id=%s 用时=%.2fs err=%s",
                idx,
                factor_total,
                fid,
                time.perf_counter() - one_t0,
                e,
            )
    logger.info(
        "阶段完成: 逐因子计算 用时=%.2fs 成功记录=%d",
        time.perf_counter() - factor_loop_t0,
        len(factor_records),
    )

    monitor_dates = sorted(
        {
            d.strftime("%Y-%m-%d")
            for d in pd.date_range(monitor_start, anchor, freq="D")
            if d in set(pd.to_datetime(price_df.index.get_level_values("trade_date").unique()))
        }
    )

    factor_root = Path(__file__).resolve().parents[2]
    t0 = time.perf_counter()
    parity = _build_parity_check(
        factor_root,
        factor_panel_for_parity,
        monitor_dates,
        universe=universe,
        sample_factor_count=parity_sample_factors,
        sample_day_count=parity_sample_days,
        tolerance=parity_tolerance,
        random_seed=random_seed,
    )
    logger.info(
        "阶段完成: 生产一致性对账 用时=%.2fs status=%s checked_pairs=%s",
        time.perf_counter() - t0,
        parity.get("status"),
        parity.get("checked_pairs"),
    )

    payload = {
        "schema_version": "1",
        "as_of_date": anchor.strftime("%Y-%m-%d"),
        "requested_as_of_date": as_of_date,
        "universe": universe,
        "window": {
            "monitor_window_trading_days": monitor_window_days,
            "start_date": monitor_start.strftime("%Y-%m-%d"),
            "end_date": anchor.strftime("%Y-%m-%d"),
        },
        "warmup_trading_days": warmup_days,
        "label_convention_ref": "qclaw_strategy_engine/docs/rollforward/label_ic_convention.md",
        "calendar_policy_ref": "qclaw_strategy_engine/docs/rollforward/calendar_policy.md",
        "data_source_mode": "db_compute_with_daily_parity_check",
        "thresholds": {
            "scene": scene,
            "source": thresholds.source,
            "version": thresholds.version,
            "ic_mean_min": thresholds.ic_mean_min,
            "ic_ir_min": thresholds.ic_ir_min,
            "coverage_min": thresholds.coverage_min,
        },
        "factors": sorted(factor_records, key=lambda x: x["factor_id"]),
        "prod_parity_check": parity,
    }

    out_dir = Path(cfg.get("paths", "output_dir", fallback="artifacts/factor_monitor"))
    out_month_dir = out_dir / anchor.strftime("%Y-%m")
    out_month_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"factor_health_{anchor.strftime('%Y-%m-%d')}"
    json_path = out_month_dir / f"{base_name}.json"
    md_path = out_month_dir / f"{base_name}.md"

    t0 = time.perf_counter()
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown_report(payload), encoding="utf-8")
    logger.info(
        "阶段完成: 报告落盘 用时=%.2fs json=%s md=%s",
        time.perf_counter() - t0,
        json_path,
        md_path,
    )
    logger.info("月监总耗时=%.2fs", time.perf_counter() - total_t0)
    return payload


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="因子工厂 P1：因子月度有效性监控")
    parser.add_argument(
        "--config",
        default="config.ini",
        help="配置文件路径（非 prod 会自动切换 _dev.ini）",
    )
    parser.add_argument(
        "--as-of-date",
        default="",
        help="锚点日期 YYYY-MM-DD，默认今天（并向前对齐交易日）",
    )
    args = parser.parse_args()

    run_factor_monitor(
        config_file=args.config,
        as_of_date=args.as_of_date.strip() or None,
    )


if __name__ == "__main__":
    main()

