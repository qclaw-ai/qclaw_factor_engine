#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
扫描 factor_docs（md）并将因子元信息写入 DB 表 factor_basic。

用途：
- 云端首次部署时，先把 md 的因子清单入库，避免后续写 factor_value_files 时触发外键缺失；
- 新增因子上线时，先跑一遍让 factor_basic 补齐。
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, Optional

from sqlalchemy import text

# 对齐仓库其他 runner：将 src 目录加入路径，便于导入 common.*
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors, FactorDefinition


logger = setup_logger("bootstrap_factor_basic", "logs/bootstrap_factor_basic.log")


def _insert_factor_basic(session, fd: FactorDefinition) -> None:
    insert_sql = text(
        """
        INSERT INTO factor_basic (
            factor_id,
            factor_name,
            factor_type,
            test_universe,
            trading_cycle,
            source_url
        ) VALUES (
            :factor_id,
            :factor_name,
            :factor_type,
            :test_universe,
            :trading_cycle,
            :source_url
        )
        ON CONFLICT (factor_id) DO UPDATE
        SET
            factor_name = EXCLUDED.factor_name,
            factor_type = EXCLUDED.factor_type,
            test_universe = EXCLUDED.test_universe,
            trading_cycle = EXCLUDED.trading_cycle,
            source_url = EXCLUDED.source_url
        """
    )

    session.execute(
        insert_sql,
        {
            "factor_id": fd.factor_id,
            "factor_name": fd.factor_name or fd.factor_id,
            "factor_type": getattr(fd, "factor_type", None),
            "test_universe": getattr(fd, "test_universe", None),
            "trading_cycle": getattr(fd, "trading_cycle", None),
            "source_url": getattr(fd, "source_url", None),
        },
    )


def run_bootstrap(config_file: str) -> None:
    _ = Config(config_file=config_file)

    factors = load_all_factors()
    if not factors:
        raise SystemExit("未解析到任何 factor_docs；请检查 [paths].factor_docs_dir 配置与文件内容")

    logger.info("factor_docs 解析完成 factors=%d", len(factors))

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    ok = 0
    fail = 0

    try:
        for fd in factors:
            try:
                _insert_factor_basic(session, fd)
                ok += 1
            except Exception as e:
                fail += 1
                logger.error("写入 factor_basic 失败 factor_id=%s err=%s", fd.factor_id, e)

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    logger.info("bootstrap 完成 ok=%d fail=%d", ok, fail)
    if fail > 0:
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="扫描 factor_docs 并写入 factor_basic")
    parser.add_argument("--config", default="config.ini", help="根配置文件路径")
    args = parser.parse_args()

    run_bootstrap(config_file=args.config)


if __name__ == "__main__":
    main()

