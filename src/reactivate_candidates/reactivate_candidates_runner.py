#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List

import numpy as np
from sqlalchemy import text

# 把 common 模块加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger

logger = setup_logger("reactivate_candidates_runner", "logs/reactivate_candidates_runner.log")


def _load_thresholds(session, scene: str) -> Dict[str, Any]:
    """加载复活所需的阈值（ic_min_reactivate / ic_ir_min_reactivate / sharpe_min_reactivate / max_drawdown_max_reactivate）"""
    sql = text(
        """
        SELECT *
        FROM factor_threshold_config
        WHERE scene = :scene AND is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
        """
    )
    row = session.execute(sql, {"scene": scene}).mappings().first()
    if not row:
        raise RuntimeError(f"未找到激活的阈值配置，scene={scene}")

    logger.info(
        "使用复活阈值配置 scene=%s, version=%s, ic_min_reactivate=%s, "
        "ic_ir_min_reactivate=%s, sharpe_min_reactivate=%s, max_drawdown_max_reactivate=%s",
        scene,
        row["version"],
        row["ic_min_reactivate"],
        row["ic_ir_min_reactivate"],
        row["sharpe_min_reactivate"],
        row["max_drawdown_max_reactivate"],
    )
    return dict(row)


def _load_reactivate_candidates(session, cooldown_days: int) -> List[str]:
    """筛选具备复活资格的因子ID列表"""
    sql = text(
        """
        SELECT factor_id
        FROM factor_basic
        WHERE is_valid = FALSE
          AND deprecate_reason = 'performance'
          AND deprecate_time IS NOT NULL
          AND deprecate_time <= (NOW() - (:cooldown_days || ' days')::interval)
        """
    )
    rows = session.execute(sql, {"cooldown_days": cooldown_days}).fetchall()
    ids = [r[0] for r in rows]
    logger.info("本次复活候选因子数量: %d, ids=%s", len(ids), ids)
    return ids


def _load_latest_backtest_for(session, factor_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """加载给定因子ID集合的最新回测记录"""
    if not factor_ids:
        return {}

    sql = text(
        """
        SELECT DISTINCT ON (factor_id)
            id,
            factor_id,
            backtest_period,
            horizon,
            ic_value,
            ic_ir,
            sharpe_ratio,
            max_drawdown,
            turnover,
            backtest_time
        FROM factor_backtest
        WHERE factor_id = ANY(:factor_ids)
        ORDER BY factor_id, backtest_time DESC, id DESC
        """
    )
    rows = session.execute(sql, {"factor_ids": factor_ids}).mappings().all()
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        latest[r["factor_id"]] = dict(r)
    logger.info("成功加载到 %d 个候选因子的最新回测记录", len(latest))
    return latest


def _should_reactivate(rec: Dict[str, Any], th: Dict[str, Any]) -> bool:
    """根据复活阈值判断是否重新启用因子"""
    ic_value = rec.get("ic_value")
    ic_ir = rec.get("ic_ir")
    sharpe = rec.get("sharpe_ratio")
    max_dd = rec.get("max_drawdown")

    def _ge(x, y):
        return x is not None and y is not None and x >= y

    def _le(x, y):
        return x is not None and y is not None and x <= y

    # IC / IC_IR / Sharpe 要 >= 对应复活阈值（若配置）
    if th.get("ic_min_reactivate") is not None and not _ge(ic_value, th["ic_min_reactivate"]):
        return False
    if th.get("ic_ir_min_reactivate") is not None and not _ge(ic_ir, th["ic_ir_min_reactivate"]):
        return False
    if th.get("sharpe_min_reactivate") is not None and not _ge(sharpe, th["sharpe_min_reactivate"]):
        return False

    # 最大回撤需要 >= 复活阈值（通常为负数，越大越好）
    if th.get("max_drawdown_max_reactivate") is not None and not _ge(
        max_dd,
        th["max_drawdown_max_reactivate"],
    ):
        return False

    return True


def run_reactivate_candidates(config_file: str = "reactivate_candidates/config.ini") -> None:
    logger.info("启动 reactivate_candidates_runner")

    cfg = Config(config_file=config_file)
    scene = cfg.get("reactivate", "scene", fallback="A_stock_daily_single_factor")
    cooldown_days = cfg.getint("reactivate", "cooldown_days", fallback=180)

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        thresholds = _load_thresholds(session, scene)

        candidates = _load_reactivate_candidates(session, cooldown_days=cooldown_days)
        if not candidates:
            logger.info("当前无满足冷却期条件的复活候选因子")
            session.commit()
            return

        latest_bt = _load_latest_backtest_for(session, candidates)
        if not latest_bt:
            logger.info("候选因子中没有最新回测记录，跳过复活流程")
            session.commit()
            return

        to_reactivate: List[str] = []

        for factor_id in candidates:
            rec = latest_bt.get(factor_id)
            if not rec:
                logger.warning("候选因子 %s 无最新回测记录，跳过", factor_id)
                continue

            decide = _should_reactivate(rec, thresholds)

            logger.info(
                "复活判断 - 因子 %s: ic=%s, ic_ir=%s, sharpe=%s, max_dd=%s, "
                "decision=%s",
                factor_id,
                rec.get("ic_value"),
                rec.get("ic_ir"),
                rec.get("sharpe_ratio"),
                rec.get("max_drawdown"),
                "REACTIVATE" if decide else "KEEP_INACTIVE",
            )

            if decide:
                to_reactivate.append(factor_id)

        if not to_reactivate:
            logger.info("本次复活流程无因子满足复活阈值")
            session.commit()
            return

        now = datetime.now()
        update_sql = text(
            """
            UPDATE factor_basic
            SET
                is_valid = TRUE,
                reactivated_time = :reactivated_time
            WHERE factor_id = ANY(:factor_ids)
            """
        )
        session.execute(
            update_sql,
            {
                "reactivated_time": now,
                "factor_ids": to_reactivate,
            },
        )

        logger.info("本次复活通过的因子数量: %d, ids=%s", len(to_reactivate), to_reactivate)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("reactivate_candidates 执行失败，已回滚: %s", e)
    finally:
        session.close()


def main():
    run_reactivate_candidates()


if __name__ == "__main__":
    main()

