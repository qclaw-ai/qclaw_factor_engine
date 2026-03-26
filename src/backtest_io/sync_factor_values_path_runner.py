#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 factor_value_files 将指定执行域的 batch_csv 路径同步到 factor_files.factor_values_path。
供仍读旧列的脚本/工具过渡；与 backtest_io 解耦（见 docs/真分域收尾与策略工厂接入_落地步骤.md P0-3）。
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text

from common.config import Config
from common.db import get_db_manager
from common.universe_service import normalize_universe_code
from common.utils import setup_logger

logger = setup_logger(
    "sync_factor_values_path_runner",
    "logs/sync_factor_values_path_runner.log",
)


def run_sync_factor_values_path_from_value_files(
    config_file: str = "src/backtest_io/config.ini",
    execution_universe: str | None = None,
) -> None:
    universe = normalize_universe_code(execution_universe or "ALL")
    logger.info("开始同步 factor_values_path，execution_universe=%s", universe)

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        select_sql = text(
            """
            SELECT DISTINCT ON (factor_id)
                factor_id, rel_path
            FROM factor_value_files
            WHERE universe = :universe
              AND artifact_type = 'batch_csv'
            ORDER BY factor_id, created_at DESC, id DESC
            """
        )
        rows = session.execute(select_sql, {"universe": universe}).fetchall()
        if not rows:
            logger.warning(
                "factor_value_files 中无 universe=%s 的 batch_csv 记录，跳过同步",
                universe,
            )
            return

        upsert_sql = text(
            """
            INSERT INTO factor_files (factor_id, doc_path, factor_values_path, log_path)
            VALUES (:factor_id, '', :factor_values_path, NULL)
            ON CONFLICT (factor_id) DO UPDATE
            SET factor_values_path = EXCLUDED.factor_values_path
            """
        )

        n = 0
        for factor_id, rel_path in rows:
            rp = (rel_path or "").strip()
            if not rp:
                continue
            session.execute(
                upsert_sql,
                {"factor_id": factor_id, "factor_values_path": rp},
            )
            n += 1

        session.commit()
        logger.info("已同步 %d 条 factor_files.factor_values_path（universe=%s）", n, universe)
    except Exception as e:
        session.rollback()
        logger.error("同步失败，已回滚: %s", e)
        raise
    finally:
        session.close()


def main() -> None:
    cfg = Config(config_file="src/backtest_io/config.ini")
    u = cfg.get("paths", "sync_factor_values_execution_universe", fallback="ALL")
    run_sync_factor_values_path_from_value_files(
        config_file="src/backtest_io/config.ini",
        execution_universe=u,
    )


if __name__ == "__main__":
    main()
