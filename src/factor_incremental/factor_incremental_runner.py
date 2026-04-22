#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.universe_service import normalize_universe_code
from common.utils import setup_logger
from factor_engine.factor_engine_runner import run_factor_engine

logger = setup_logger("factor_incremental_runner", "logs/factor_incremental_runner.log")


def _get_last_batch_end_date(config_file: str, universe: str) -> Optional[str]:
    """读取指定 universe 下 batch_csv 的最大 date_end。"""
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()
    try:
        row = session.execute(
            text(
                """
                SELECT MAX(date_end) AS max_date_end
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'batch_csv'
                """
            ),
            {"universe": universe},
        ).mappings().first()
        if not row or not row["max_date_end"]:
            return None
        return str(row["max_date_end"])
    finally:
        session.close()


def _shift_trading_date(config_file: str, anchor_date: str, shift_days: int) -> str:
    """按交易日向前平移，返回平移后的交易日（不足时返回可用最早交易日）。"""
    if shift_days <= 0:
        return anchor_date

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()
    try:
        rows = session.execute(
            text(
                """
                SELECT trade_date
                FROM (
                    SELECT DISTINCT trade_date
                    FROM stock_daily
                    WHERE trade_date <= :anchor_date
                    ORDER BY trade_date DESC
                    LIMIT :limit_n
                ) t
                ORDER BY trade_date ASC
                """
            ),
            {"anchor_date": anchor_date, "limit_n": shift_days + 1},
        ).fetchall()
        if not rows:
            return anchor_date
        return str(rows[0][0])
    finally:
        session.close()


def run_factor_incremental(
    config_file: str = "config.ini",
    *,
    as_of_date: Optional[str] = None,
    mode: Optional[str] = None,
    stage: Optional[str] = None,
    batch_id: Optional[str] = None,
    warmup_trading_days: Optional[int] = None,
) -> None:
    """增量/重算编排入口。

    - incremental: 从 last_date_end + 1 到 as_of_date
    - rebase: 使用 factor_engine 配置中的 start_date 到 as_of_date
    """
    cfg = Config(config_file=config_file)
    factor_engine_config_file = cfg.get(
        "factor_incremental",
        "factor_engine_config_file",
        fallback="config.ini",
    )
    fe_cfg = Config(config_file=factor_engine_config_file)
    universe = normalize_universe_code(fe_cfg.get("factor_engine", "universe", fallback="ALL"))
    base_start_date = fe_cfg.get("factor_engine", "start_date", fallback="2024-01-01")
    run_end_date = as_of_date or datetime.now().strftime("%Y-%m-%d")

    mode = (mode or cfg.get("factor_incremental", "mode", fallback="incremental")).strip().lower()
    if mode not in ("incremental", "rebase"):
        raise ValueError(f"mode 仅支持 incremental/rebase，当前={mode}")
    stage = (stage or cfg.get("factor_incremental", "stage", fallback="candidate")).strip().lower()
    if stage not in ("candidate", "production", "deprecated"):
        raise ValueError(f"stage 不合法：{stage}")
    effective_warmup_days = (
        int(warmup_trading_days)
        if warmup_trading_days is not None
        else int(cfg.getint("factor_incremental", "warmup_trading_days", fallback=200) or 200)
    )

    if mode == "rebase":
        run_start_date = base_start_date
        is_rebase = True
    else:
        last_end = _get_last_batch_end_date(config_file=factor_engine_config_file, universe=universe)
        if last_end:
            run_start_date = (datetime.strptime(last_end, "%Y-%m-%d") + timedelta(days=1)).strftime(
                "%Y-%m-%d"
            )
        else:
            run_start_date = base_start_date
        is_rebase = False

    calc_start_date = _shift_trading_date(
        config_file=factor_engine_config_file,
        anchor_date=run_start_date,
        shift_days=effective_warmup_days,
    )

    if batch_id:
        resolved_batch_id = batch_id
    else:
        prefix = "rebase" if is_rebase else "inc"
        resolved_batch_id = f"{prefix}_{run_end_date.replace('-', '')}"

    logger.info(
        (
            "增量编排启动 mode=%s universe=%s run_start=%s calc_start=%s end_date=%s "
            "warmup_trading_days=%s stage=%s batch_id=%s is_rebase=%s factor_engine_config=%s"
        ),
        mode,
        universe,
        run_start_date,
        calc_start_date,
        run_end_date,
        effective_warmup_days,
        stage,
        resolved_batch_id,
        is_rebase,
        factor_engine_config_file,
    )

    run_factor_engine(
        config_file=factor_engine_config_file,
        start_date_override=calc_start_date,
        end_date_override=run_end_date,
        publish_start_date_override=run_start_date,
        batch_id_override=resolved_batch_id,
        stage_override=stage,
        is_rebase_override=is_rebase,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="因子值增量/重算编排入口")
    parser.add_argument(
        "--config",
        default="config.ini",
        help="factor_incremental 配置文件路径（非 prod 自动切换 _dev.ini）",
    )
    parser.add_argument("--as-of-date", default=None, help="批次结束日期，默认今天（YYYY-MM-DD）")
    parser.add_argument(
        "--mode",
        default="incremental",
        choices=["incremental", "rebase"],
        help="incremental=增量，rebase=重算",
    )
    parser.add_argument(
        "--stage",
        default=None,
        choices=["candidate", "production", "deprecated"],
        help="覆盖配置中的 stage（candidate/production/deprecated）",
    )
    parser.add_argument("--batch-id", default=None, help="自定义批次号（可选）")
    parser.add_argument(
        "--warmup-trading-days",
        type=int,
        default=None,
        help="覆盖配置中的 warmup_trading_days",
    )
    args = parser.parse_args()

    run_factor_incremental(
        config_file=args.config,
        as_of_date=args.as_of_date,
        mode=args.mode,
        stage=args.stage,
        batch_id=args.batch_id,
        warmup_trading_days=args.warmup_trading_days,
    )


if __name__ == "__main__":
    main()

