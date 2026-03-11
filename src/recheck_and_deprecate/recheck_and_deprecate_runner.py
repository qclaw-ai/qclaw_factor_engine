#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from datetime import datetime
from typing import Dict, Any

import numpy as np
from sqlalchemy import text

# 把 common 模块加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger

logger = setup_logger("recheck_and_deprecate_runner", "logs/recheck_and_deprecate_runner.log")


def _load_thresholds(session, scene: str) -> Dict[str, Any]:
    """加载复检所需的阈值（ic_decay_threshold / latest_ic_min）"""
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
        f"使用复检阈值配置 scene={scene}, version={row['version']}, "
        f"ic_decay_threshold={row['ic_decay_threshold']}, latest_ic_min={row['latest_ic_min']}"
    )
    return dict(row)


def _load_init_and_latest_ic(session) -> Dict[str, Dict[str, Any]]:
    """加载每个当前 is_valid=true 因子的初始与最新 IC"""
    sql = text(
        """
        WITH fb AS (
            SELECT b.*
            FROM factor_backtest b
            JOIN factor_basic f ON f.factor_id = b.factor_id
            WHERE f.is_valid = TRUE
        ),
        init AS (
            SELECT DISTINCT ON (factor_id)
                factor_id,
                ic_value AS ic_init,
                id       AS init_id,
                backtest_time AS init_time
            FROM fb
            ORDER BY factor_id, backtest_time ASC, id ASC
        ),
        latest AS (
            SELECT DISTINCT ON (factor_id)
                factor_id,
                ic_value AS ic_latest,
                id       AS latest_id,
                backtest_time AS latest_time
            FROM fb
            ORDER BY factor_id, backtest_time DESC, id DESC
        )
        SELECT
            i.factor_id,
            i.ic_init,
            l.ic_latest,
            i.init_id,
            l.latest_id,
            i.init_time,
            l.latest_time
        FROM init i
        JOIN latest l USING (factor_id)
        """
    )
    rows = session.execute(sql).mappings().all()
    data: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        data[r["factor_id"]] = dict(r)

    logger.info(f"共加载到 {len(data)} 个候选复检因子（当前 is_valid=true）")
    return data


def _should_deprecate(
    ic_init: float | None,
    ic_latest: float | None,
    ic_decay_threshold: float | None,
    latest_ic_min: float | None,
) -> bool:
    """根据 IC 衰减与最新 IC 判断是否过时"""
    if ic_init is None or ic_latest is None:
        return False
    if ic_decay_threshold is None and latest_ic_min is None:
        return False

    # 如果初始 IC 不大于 0，很难定义“衰减比例”，直接用最新 IC 阈值判断
    if ic_init <= 0 or np.isclose(ic_init, 0):
        if latest_ic_min is not None and ic_latest < latest_ic_min:
            return True
        return False

    ic_decay = (ic_init - ic_latest) / ic_init

    cond_decay = (
        ic_decay_threshold is not None
        and ic_decay > ic_decay_threshold
    )
    cond_latest = (
        latest_ic_min is not None
        and ic_latest < latest_ic_min
    )

    return bool(cond_decay and cond_latest)


def run_recheck_and_deprecate(config_file: str = "recheck_and_deprecate/config.ini") -> None:
    logger.info("启动 recheck_and_deprecate_runner")

    cfg = Config(config_file=config_file)
    scene = cfg.get("recheck", "scene", fallback="A_stock_daily_single_factor")

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        thresholds = _load_thresholds(session, scene)
        ic_decay_threshold = thresholds.get("ic_decay_threshold")
        latest_ic_min = thresholds.get("latest_ic_min")

        factor_ic = _load_init_and_latest_ic(session)
        if not factor_ic:
            logger.info("没有可复检的因子（当前 is_valid=true 的因子无回测记录）")
            return

        deprecate_ids: list[str] = []

        for factor_id, rec in factor_ic.items():
            ic_init = rec.get("ic_init")
            ic_latest = rec.get("ic_latest")

            to_deprecate = _should_deprecate(
                ic_init=ic_init,
                ic_latest=ic_latest,
                ic_decay_threshold=ic_decay_threshold,
                latest_ic_min=latest_ic_min,
            )

            logger.info(
                f"复检因子 {factor_id}: ic_init={ic_init}, ic_latest={ic_latest}, "
                f"decay_threshold={ic_decay_threshold}, latest_ic_min={latest_ic_min}, "
                f"decision={'DEPRECATE' if to_deprecate else 'KEEP'}"
            )

            if to_deprecate:
                deprecate_ids.append(factor_id)

        if not deprecate_ids:
            logger.info("本次复检无需要淘汰的因子")
            session.commit()
            return

        # 更新被判定为过时的因子
        now = datetime.now()
        update_sql = text(
            """
            UPDATE factor_basic
            SET
                is_valid = FALSE,
                deprecate_reason = COALESCE(deprecate_reason, 'performance'),
                deprecate_time = :deprecate_time
            WHERE factor_id = ANY(:factor_ids)
            """
        )
        session.execute(
            update_sql,
            {
                "deprecate_time": now,
                "factor_ids": deprecate_ids,
            },
        )

        logger.info(f"本次复检共淘汰因子数量: {len(deprecate_ids)}, ids={deprecate_ids}")
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"recheck_and_deprecate 执行失败，已回滚: {e}")
    finally:
        session.close()


def main():
    run_recheck_and_deprecate()


if __name__ == "__main__":
    main()

