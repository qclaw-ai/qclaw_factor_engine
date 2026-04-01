#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
日更线：按「因子值所属交易日 T」生成单日因子长表 CSV，并更新日更路径元数据。

- 主登记：factor_value_files（artifact_type=daily_csv, 含 universe 维度）。
- 与评估线隔离：不修改 factor_values_path（月更/大回测由 backtest_io 维护）。
- 复用 factor_engine_runner 的行情加载与公式计算、截面去极值+标准化。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Set

import pandas as pd
from sqlalchemy import text

# 对齐：把 src 加入路径（common / factor_engine / factor_docs）
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors, FactorDefinition
from factor_engine.factor_engine_runner import (
    _load_stock_daily,
    compute_factor_values,
    winsorize_and_standardize,
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


def _project_root() -> Path:
    """qclaw_factor_engine 仓库根目录（src 的上一级）。"""
    return Path(__file__).resolve().parents[2]


def _resolve_trade_date_to_available(
    requested: str,
    avail_dates,
) -> tuple[str, pd.Timestamp]:
    """
    若请求日不在行情交易日集合中，则自动对齐：
    - 优先取「不大于请求日的最新交易日」；
    - 若请求日早于全部行情，则取行情中的最早日（并打 WARNING）。
    """
    t_req = pd.Timestamp(requested.strip())
    uniq = pd.unique(pd.to_datetime(avail_dates))
    uniq.sort()
    as_set = set(uniq)

    if t_req in as_set:
        return requested.strip(), t_req

    before = uniq[uniq <= t_req]
    if len(before) > 0:
        resolved = pd.Timestamp(before[-1])
        logger.warning(
            "trade_date=%s 不在行情数据中，已自动对齐为最近可用交易日 %s",
            requested.strip(),
            resolved.strftime("%Y-%m-%d"),
        )
        return resolved.strftime("%Y-%m-%d"), resolved

    earliest = pd.Timestamp(uniq[0])
    logger.warning(
        "trade_date=%s 早于行情最早日 %s，已自动对齐为最早可用日",
        requested.strip(),
        earliest.strftime("%Y-%m-%d"),
    )
    return earliest.strftime("%Y-%m-%d"), earliest


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


def _upsert_factor_value_files_daily(
    session,
    *,
    factor_id: str,
    universe: str,
    rel_path_posix: str,
    trade_date: str,
) -> None:
    """
    真分域主登记：写 factor_value_files（daily_csv）。
    约定：同一 (factor_id, universe, artifact_type=daily_csv) 仅保留一条最新路径记录。
    """
    updated = session.execute(
        text(
            """
            UPDATE factor_value_files
            SET rel_path = :rel_path,
                trade_date = :trade_date,
                date_start = NULL,
                date_end = NULL,
                created_at = CURRENT_TIMESTAMP
            WHERE factor_id = :factor_id
              AND universe = :universe
              AND artifact_type = 'daily_csv'
              AND trade_date = :trade_date
            """
        ),
        {
            "factor_id": factor_id,
            "universe": universe,
            "rel_path": rel_path_posix,
            "trade_date": trade_date,
        },
    )

    if int(getattr(updated, "rowcount", 0) or 0) > 0:
        return

    session.execute(
        text(
            """
            INSERT INTO factor_value_files (
                factor_id,
                universe,
                artifact_type,
                rel_path,
                trade_date,
                created_at
            )
            VALUES (
                :factor_id,
                :universe,
                'daily_csv',
                :rel_path,
                :trade_date,
                CURRENT_TIMESTAMP
            )
            """
        ),
        {
            "factor_id": factor_id,
            "universe": universe,
            "rel_path": rel_path_posix,
            "trade_date": trade_date,
        },
    )


def _parse_factor_ids_csv(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def run_daily_factor_values(
    config_file: str,
    trade_date: str,
    lookback_days: int,
    factor_ids_filter: List[str] | None,
    scope: str = "valid_only",
    universe: str = "ALL",
) -> None:
    """
    :param trade_date: 因子值所属交易日 T（YYYY-MM-DD），写入 CSV 的 trade_date 列；
        若不在已加载的行情交易日中，会对齐为「不大于 T 的最近交易日」（早于最早日则对齐最早日）。
    :param lookback_days: 向前拉取行情的自然日天数，用于 rolling/REF 等算子。
    :param scope: valid_only=仅 is_valid；all_in_basic=factor_basic 全量 ∩ factor_docs（日更路径可覆盖未过阈因子）。
    :param universe: 本次日更因子值所属域（ALL/HS300/...，会做 ALL_A -> ALL 归一）。
    """
    cfg = Config(config_file=config_file)
    db_manager = get_db_manager(config_file=config_file)

    requested_trade_date = trade_date.strip()
    t_ts = pd.Timestamp(requested_trade_date)
    start_ts = t_ts - pd.Timedelta(days=int(lookback_days))
    start_date = start_ts.strftime("%Y-%m-%d")
    end_date = t_ts.strftime("%Y-%m-%d")

    u_tag = _normalize_universe_code(universe)

    logger.info(
        "日更因子值启动 requested_trade_date=%s, universe=%s, 首次拉取行情窗口 [%s, %s], lookback_days=%s",
        requested_trade_date,
        u_tag,
        start_date,
        end_date,
        lookback_days,
    )

    meta_list = load_all_factors()
    meta_by_id: dict[str, FactorDefinition] = {f.factor_id: f for f in meta_list}
    if not meta_by_id:
        logger.error("factor_docs 未解析到任何因子，退出")
        return

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

    logger.info(
        "本次将计算 %d 个因子（factor_basic 选中集合 ∩ factor_docs）；"
        "若红框因子未出现，多为 is_valid=FALSE 且此前使用了 valid_only",
        len(factors_to_run),
    )

    price_df = _load_stock_daily(
        config_file=config_file,
        start_date=start_date,
        end_date=end_date,
    )
    if price_df.empty:
        logger.error("stock_daily 在窗口内无数据，无法计算")
        return

    avail_dates = price_df.index.get_level_values("trade_date").unique()
    trade_date, t_ts = _resolve_trade_date_to_available(requested_trade_date, avail_dates)

    # lookback 以「生效交易日」为终点，重切行情窗口（避免请求日为非交易日时锚错日历日）
    start_ts_eff = t_ts - pd.Timedelta(days=int(lookback_days))
    td_lvl = price_df.index.get_level_values("trade_date")
    mask = (td_lvl >= start_ts_eff) & (td_lvl <= t_ts)
    price_df = price_df.loc[mask]

    if trade_date != requested_trade_date:
        logger.info(
            "日更 trade_date 生效=%s（请求=%s），行情窗口 [%s, %s] 已按生效日重算",
            trade_date,
            requested_trade_date,
            start_ts_eff.strftime("%Y-%m-%d"),
            t_ts.strftime("%Y-%m-%d"),
        )

    if price_df.empty:
        logger.error(
            "对齐 trade_date=%s 后行情窗口为空；请检查 lookback_days 与 stock_daily 覆盖范围",
            trade_date,
        )
        return

    root = _project_root()
    out_base = root / "factor_values" / "daily" / "by_universe" / u_tag / trade_date

    session = db_manager.get_session()
    ok_count = 0
    fail_count = 0

    try:
        for factor in factors_to_run:
            logger.info("日更计算因子 %s - %s", factor.factor_id, factor.factor_name)
            try:
                raw_series = compute_factor_values(factor.formula, price_df)
                processed = winsorize_and_standardize(raw_series)
                df_out = processed.to_frame("factor_value").reset_index()
                df_out["trade_date"] = pd.to_datetime(df_out["trade_date"])
                df_day = df_out[df_out["trade_date"] == t_ts].copy()

                if df_day.empty:
                    n_t = int((df_out["trade_date"] == t_ts).sum())
                    nn = int(df_out.loc[df_out["trade_date"] == t_ts, "factor_value"].notna().sum())
                    logger.warning(
                        "因子 %s 在 %s 截面无输出：trade_date 匹配行数=%d，其中 factor_value 非空=%d（"
                        "若为 0 多为历史窗口不足，可增大 lookback_days）",
                        factor.factor_id,
                        trade_date,
                        n_t,
                        nn,
                    )
                    fail_count += 1
                    continue

                df_day = df_day[["trade_date", "stock_code", "factor_value"]]
                df_day["trade_date"] = df_day["trade_date"].dt.strftime("%Y-%m-%d")

                out_base.mkdir(parents=True, exist_ok=True)
                csv_name = f"{factor.factor_id}.csv"
                full_path = out_base / csv_name
                df_day.to_csv(full_path, index=False)

                rel = full_path.resolve().relative_to(root.resolve()).as_posix()

                # 主登记：写真分域日更路径（artifact_type=daily_csv）。
                _upsert_factor_value_files_daily(
                    session,
                    factor_id=factor.factor_id,
                    universe=u_tag,
                    rel_path_posix=rel,
                    trade_date=trade_date,
                )
                logger.info(
                    "已写出 %s，并更新 factor_value_files(daily_csv)=%s",
                    full_path,
                    rel,
                )
                ok_count += 1

            except Exception as e:
                logger.error("因子 %s 日更失败: %s", factor.factor_id, e)
                fail_count += 1

        session.commit()
        logger.info(
            "日更完成：成功 %d，失败/跳过 %d，已提交 DB",
            ok_count,
            fail_count,
        )
    except Exception as e:
        session.rollback()
        logger.error("日更批量执行失败，已回滚: %s", e)
        raise
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="因子工厂：日更 factor_values（单日 CSV + factor_values_path_daily）")
    parser.add_argument(
        "--config",
        default="src/daily_factor_values/config_dev.ini",
        help="配置文件路径（需含 [database]，与 factor_engine 一致）",
    )
    parser.add_argument(
        "--trade-date",
        default="",
        help="因子值所属交易日 T，格式 YYYY-MM-DD（写入 CSV 的 trade_date）；未填则使用当天日期",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help="向前拉行情的自然日天数；默认从配置 [daily] lookback_days 读取，缺省为 380",
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
    lookback = args.lookback_days
    if lookback is None:
        lookback = cfg.getint("daily", "lookback_days", fallback=380)

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
        lookback_days=lookback,
        factor_ids_filter=filt,
        scope=scope,
        universe=universe,
    )


if __name__ == "__main__":
    main()
