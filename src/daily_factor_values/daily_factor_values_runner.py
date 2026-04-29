#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日更线：按「因子值所属交易日 T」生成单日因子长表 CSV，并更新日更路径元数据。

- 主登记：factor_value_files（artifact_type=daily_csv, 含 universe 维度）。
- 与评估线隔离：不修改 factor_values_path（月更/大回测由 backtest_io 维护）。
- 计算链路：对齐 factor_incremental_runner（收成 end_date → 交易日 warmup 窗口 → factor_engine_runner daily_csv_mode）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import List, Optional, Set

from sqlalchemy import text

# 对齐：把 src 加入路径（common / factor_engine / factor_docs）
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors, FactorDefinition
from factor_engine.factor_engine_runner import run_factor_engine
from factor_incremental.factor_incremental_runner import (
    _clamp_requested_end_to_quotes,
    _shift_trading_date,
)

logger = setup_logger("daily_factor_values_runner", "logs/daily_factor_values_runner.log")


def _normalize_universe_code(universe: str | None) -> str:
    """与双工厂约定对齐：缺省 ALL，历史 ALL_A -> ALL。"""
    u = (universe or "").strip().upper()
    if not u:
        return "ALL"
    if u == "ALL_A":
        return "ALL"
    return u


def _load_valid_factor_ids_from_db(session) -> List[str]:
    """从 factor_basic 读取 is_valid = TRUE 的因子列表。"""
    sql = text(
        """
        SELECT factor_id
        FROM factor_basic
        WHERE is_valid = TRUE
        ORDER BY factor_id
        """
    )
    rows = session.execute(sql).fetchall()
    return [r[0] for r in rows]


def _load_all_factor_ids_from_basic(session) -> List[str]:
    """从 factor_basic 读取全部 factor_id（含 is_valid=FALSE），用于与「全量回测有 CSV」对齐。"""
    sql = text(
        """
        SELECT factor_id
        FROM factor_basic
        ORDER BY factor_id
        """
    )
    rows = session.execute(sql).fetchall()
    return [r[0] for r in rows]


def _parse_factor_ids_csv(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def run_daily_factor_values(
    config_file: str,
    trade_date: str,
    factor_ids_filter: List[str] | None,
    scope: str = "valid_only",
    universe: str = "ALL",
    *,
    warmup_trading_days: Optional[int] = None,
) -> None:
    """
    :param trade_date: 请求的因子发布日（先与 stock_daily 收成最晚交易日，再给 factor_engine）。
    :param scope: valid_only=仅 is_valid；all_in_basic=factor_basic 全量 ∩ factor_docs（日更路径可覆盖未过阈因子）。
    :param universe: 本次日更所属域；覆盖 [factor_engine] 中 universe。
    :param warmup_trading_days: 可选，覆盖 [factor_incremental].warmup_trading_days。
    """
    cfg_root = Config(config_file=config_file)
    factor_engine_config_file = cfg_root.get(
        "factor_incremental",
        "factor_engine_config_file",
        fallback="config.ini",
    )
    meta_list = load_all_factors()
    meta_by_id: dict[str, FactorDefinition] = {f.factor_id: f for f in meta_list}
    if not meta_by_id:
        logger.error("factor_docs 未解析到任何因子，退出")
        return

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()
    try:
        if scope == "all_in_basic":
            db_ids = _load_all_factor_ids_from_basic(session)
            logger.info("scope=all_in_basic：从 factor_basic 加载 %d 个 factor_id", len(db_ids))
        else:
            db_ids = _load_valid_factor_ids_from_db(session)
            logger.info("scope=valid_only：从 factor_basic 加载 is_valid=TRUE 共 %d 个", len(db_ids))
    finally:
        session.close()

    if not db_ids:
        logger.warning("factor_basic 无可用 factor_id，退出（请检查 scope 与库数据）")
        return

    id_set: Set[str] = set(db_ids)
    if factor_ids_filter:
        wanted = set(factor_ids_filter)
        id_set &= wanted
        missing_docs = wanted - set(meta_by_id.keys())
        if missing_docs:
            logger.warning("以下因子在 factor_docs 中不存在，将跳过: %s", sorted(missing_docs))

    factors_to_run: List[FactorDefinition] = [
        meta_by_id[fid] for fid in sorted(id_set) if fid in meta_by_id
    ]

    if not factors_to_run:
        logger.error("无待计算因子（检查 is_valid 与 factor_docs 是否交集为空）")
        return

    effective_warmup = (
        int(warmup_trading_days)
        if warmup_trading_days is not None
        else int(cfg_root.getint("factor_incremental", "warmup_trading_days", fallback=200) or 200)
    )

    requested_trade_date = trade_date.strip()
    end_eff, _ = _clamp_requested_end_to_quotes(
        requested_trade_date,
        config_file=factor_engine_config_file,
    )
    anchor_date_t = end_eff.strip()[:10]
    calc_start_date = _shift_trading_date(
        config_file=factor_engine_config_file,
        anchor_date=anchor_date_t,
        shift_days=effective_warmup,
    )

    u_tag = _normalize_universe_code(universe)
    stage_eff = (
        (cfg_root.get("factor_incremental", "stage", fallback="candidate") or "candidate")
        .strip()
        .lower()
    )

    fid_list = [f.factor_id for f in factors_to_run]
    resolved_batch_id = f"daily_{anchor_date_t.replace('-', '')}"

    logger.info(
        "日更（对齐 incremental+warmup）requested=%s effective_end=%s calc_start=%s warmup_trading_days=%s "
        "universe=%s stage=%s factor_engine_config=%s batch_id=%s factors=%s",
        requested_trade_date,
        anchor_date_t,
        calc_start_date,
        effective_warmup,
        u_tag,
        stage_eff,
        factor_engine_config_file,
        resolved_batch_id,
        len(fid_list),
    )

    run_factor_engine(
        config_file=factor_engine_config_file,
        start_date_override=calc_start_date,
        end_date_override=anchor_date_t,
        publish_start_date_override=anchor_date_t,
        batch_id_override=resolved_batch_id,
        stage_override=stage_eff,
        is_rebase_override=False,
        factor_ids_override=fid_list,
        universe_override=u_tag,
        skip_if_batch_csv_record_exists_override=None,
        daily_csv_mode=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="因子工厂：日更 factor_values（单日 CSV + factor_values_path_daily）")
    parser.add_argument(
        "--config",
        default="config.ini",
        help="配置文件路径（需含 [database]，与 factor_engine 一致）",
    )
    parser.add_argument(
        "--trade-date",
        default="",
        help="因子值所属交易日 T，格式 YYYY-MM-DD（写入 CSV 的 trade_date）；未填则使用当天日期",
    )
    parser.add_argument(
        "--warmup-trading-days",
        type=int,
        default=None,
        help="覆盖 [factor_incremental].warmup_trading_days（默认 200）；用于计算窗口起点（交易日前移）。不传则从配置读取。",
    )
    parser.add_argument(
        "--factor-ids",
        default="",
        help="可选：逗号分隔，仅跑这些因子（用于联调）；为空则按 --scope 决定因子集合",
    )
    parser.add_argument(
        "--scope",
        choices=("valid_only", "all_in_basic"),
        default=None,
        help="valid_only=仅 is_valid（默认）；all_in_basic=factor_basic 全量∩docs，给未过阈但仍有回测 CSV 的因子写日更",
    )
    parser.add_argument(
        "--universe",
        default="",
        help="本次日更所属域（如 ALL/HS300，支持历史 ALL_A 自动归一到 ALL）",
    )

    args = parser.parse_args()

    cfg = Config(config_file=args.config)

    scope = args.scope or cfg.get("daily", "scope", fallback="all_in_basic").strip()
    if scope not in ("valid_only", "all_in_basic"):
        scope = "all_in_basic"

    universe = args.universe.strip() or cfg.get("daily", "universe", fallback="ALL").strip()
    filt = _parse_factor_ids_csv(args.factor_ids) if args.factor_ids.strip() else None

    trade_date = args.trade_date.strip()
    if not trade_date:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    run_daily_factor_values(
        config_file=args.config,
        trade_date=trade_date,
        factor_ids_filter=filt,
        scope=scope,
        universe=universe,
        warmup_trading_days=args.warmup_trading_days,
    )


if __name__ == "__main__":
    main()
