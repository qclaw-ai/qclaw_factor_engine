#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
from datetime import datetime
from typing import Dict

# 把 common / backtest_core / factor_docs 加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text

from backtest_core.backtest_core_runner import run_backtest, BacktestResult
from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors, FactorDefinition

logger = setup_logger("backtest_io_runner", "logs/backtest_io_runner.log")


def _load_factor_meta() -> Dict[str, FactorDefinition]:
    """从 factor_docs 加载因子元数据，便于写入 factor_basic / JSON"""
    factors = load_all_factors()
    return {f.factor_id: f for f in factors}


def _ensure_factor_basic(
    session,
    meta: Dict[str, FactorDefinition],
    factor_id: str,
) -> None:
    """确保 factor_basic 中存在该因子记录（若无则插入一条最小记录）"""
    fd = meta.get(factor_id)

    factor_name = fd.factor_name if fd else factor_id
    factor_type = fd.factor_type if fd else None
    test_universe = fd.test_universe if fd else None
    trading_cycle = fd.trading_cycle if fd else None
    source_url = fd.source_url if fd else None

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
        ON CONFLICT (factor_id) DO NOTHING
        """
    )

    session.execute(
        insert_sql,
        {
            "factor_id": factor_id,
            "factor_name": factor_name,
            "factor_type": factor_type,
            "test_universe": test_universe,
            "trading_cycle": trading_cycle,
            "source_url": source_url,
        },
    )


def _insert_factor_backtest(session, res: BacktestResult) -> None:
    """将回测结果插入 factor_backtest 表"""
    insert_sql = text(
        """
        INSERT INTO factor_backtest (
            factor_id,
            backtest_period,
            horizon,
            ic_value,
            ic_ir,
            sharpe_ratio,
            max_drawdown,
            turnover,
            pass_standard,
            comment
        ) VALUES (
            :factor_id,
            :backtest_period,
            :horizon,
            :ic_value,
            :ic_ir,
            :sharpe_ratio,
            :max_drawdown,
            :turnover,
            :pass_standard,
            :comment
        )
        """
    )

    session.execute(
        insert_sql,
        {
            "factor_id": res.factor_id,
            "backtest_period": res.backtest_period,
            "horizon": res.horizon,
            "ic_value": res.ic_value,
            "ic_ir": res.ic_ir,
            "sharpe_ratio": res.sharpe_ratio,
            "max_drawdown": res.max_drawdown,
            "turnover": res.turnover,
            "pass_standard": None,  # 是否通过标准由 selection_and_store 再更新
            "comment": None,
        },
    )


def _write_backtest_json(
    base_dir: str,
    res: BacktestResult,
    meta: Dict[str, FactorDefinition],
) -> str:
    """将单个因子回测结果写入 JSON，返回路径"""
    os.makedirs(base_dir, exist_ok=True)
    file_name = f"{res.factor_id}_backtest.json"
    path = os.path.join(base_dir, file_name)

    fd = meta.get(res.factor_id)

    payload = {
        "factor_id": res.factor_id,
        "factor_name": fd.factor_name if fd else res.factor_id,
        "factor_type": fd.factor_type if fd else None,
        "test_universe": fd.test_universe if fd else None,
        "trading_cycle": fd.trading_cycle if fd else None,
        "source_url": fd.source_url if fd else None,
        "backtest_period": res.backtest_period,
        "horizon": res.horizon,
        "key_metrics": {
            "ic_value": res.ic_value,
            "ic_ir": res.ic_ir,
            "sharpe_ratio": res.sharpe_ratio,
            "max_drawdown": res.max_drawdown,
            "turnover": res.turnover,
        },
        "pass_standard": None,
        "backtest_time": datetime.now().isoformat(),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return path


def run_backtest_io(
    io_config_file: str = "backtest_io/config.ini",
    core_config_file: str = "backtest_core/config.ini",
) -> None:
    logger.info("启动 backtest_io_runner")

    cfg = Config(config_file=io_config_file)
    backtest_results_dir = cfg.get(
        "paths",
        "backtest_results_dir",
        fallback="backtest_results",
    )

    factor_meta = _load_factor_meta()
    logger.info(f"已加载 {len(factor_meta)} 个因子元数据")

    # 先跑回测，获得所有因子的回测结果
    results = run_backtest(config_file=core_config_file)
    if not results:
        logger.warning("未获得任何回测结果，结束 backtest_io")
        return

    db_manager = get_db_manager(config_file=io_config_file)
    session = db_manager.get_session()

    try:
        for res in results:
            logger.info(f"处理 backtest_io，因子: {res.factor_id}")

            # 1) 写 JSON
            json_path = _write_backtest_json(
                base_dir=backtest_results_dir,
                res=res,
                meta=factor_meta,
            )
            logger.info(f"回测结果 JSON 写入: {json_path}")

            # 2) 确保 factor_basic 中有记录
            _ensure_factor_basic(session, factor_meta, res.factor_id)

            # 3) 插入 factor_backtest
            _insert_factor_backtest(session, res)

        session.commit()
        logger.info("backtest_io 全部写入 DB 成功")
    except Exception as e:
        session.rollback()
        logger.error(f"backtest_io 执行失败，已回滚: {e}")
    finally:
        session.close()


def main():
    run_backtest_io()


if __name__ == "__main__":
    main()

