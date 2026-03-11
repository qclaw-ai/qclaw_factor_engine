#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from typing import Dict, Any

# 把 common / factor_docs 加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors, FactorDefinition

logger = setup_logger("selection_and_store_runner", "logs/selection_and_store_runner.log")


def _load_thresholds(session, scene: str) -> Dict[str, Any]:
    """加载当前场景下激活的阈值配置"""
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
    logger.info(f"使用阈值配置 scene={scene}, version={row['version']}")
    return dict(row)


def _load_latest_backtests(session) -> Dict[str, Dict[str, Any]]:
    """加载每个因子最新一条 factor_backtest 记录"""
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
            pass_standard,
            backtest_time
        FROM factor_backtest
        ORDER BY factor_id, backtest_time DESC, id DESC
        """
    )
    rows = session.execute(sql).mappings().all()
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        latest[r["factor_id"]] = dict(r)
    logger.info(f"共加载到 {len(latest)} 个因子的最新回测记录")
    return latest


def _judge_pass(res: Dict[str, Any], th: Dict[str, Any]) -> bool:
    """根据阈值判断因子是否通过入库标准"""
    ic_value = res.get("ic_value")
    ic_ir = res.get("ic_ir")
    sharpe = res.get("sharpe_ratio")
    max_dd = res.get("max_drawdown")
    turnover = res.get("turnover")

    def _ge(x, y):
        return x is not None and y is not None and x >= y

    def _le(x, y):
        return x is not None and y is not None and x <= y

    # 只在阈值非空时启用该约束
    if th.get("ic_min") is not None and not _ge(ic_value, th["ic_min"]):
        return False
    if th.get("ic_ir_min") is not None and not _ge(ic_ir, th["ic_ir_min"]):
        return False
    if th.get("sharpe_min") is not None and not _ge(sharpe, th["sharpe_min"]):
        return False
    # max_drawdown_max 为“最大允许回撤”（负数），回测结果需要 >= 该阈值
    if th.get("max_drawdown_max") is not None and not _ge(max_dd, th["max_drawdown_max"]):
        return False
    if th.get("turnover_max") is not None and not _le(turnover, th["turnover_max"]):
        return False

    return True


def _upsert_factor_files(
    session,
    factor_id: str,
    doc_path: str | None,
    backtest_json_path: str | None,
) -> None:
    """更新 factor_files 的 json 路径（若不存在则插入一条记录）"""
    insert_sql = text(
        """
        INSERT INTO factor_files (
            factor_id,
            doc_path,
            backtest_json_path,
            log_path
        ) VALUES (
            :factor_id,
            :doc_path,
            :backtest_json_path,
            :log_path
        )
        ON CONFLICT (factor_id) DO UPDATE SET
            doc_path = COALESCE(EXCLUDED.doc_path, factor_files.doc_path),
            backtest_json_path = COALESCE(EXCLUDED.backtest_json_path, factor_files.backtest_json_path)
        """
    )

    session.execute(
        insert_sql,
        {
            "factor_id": factor_id,
            "doc_path": doc_path,
            "backtest_json_path": backtest_json_path,
            "log_path": None,
        },
    )


def run_selection_and_store(config_file: str = "selection_and_store/config.ini") -> None:
    logger.info("启动 selection_and_store_runner")

    cfg = Config(config_file=config_file)
    scene = cfg.get("selection", "scene", fallback="A_stock_daily_single_factor")
    backtest_results_dir = cfg.get(
        "paths",
        "backtest_results_dir",
        fallback="backtest_results",
    )

    # 加载因子元数据（用于 doc_path / 名称等）
    factor_meta_list = load_all_factors()
    factor_meta: Dict[str, FactorDefinition] = {f.factor_id: f for f in factor_meta_list}
    logger.info(f"已加载 {len(factor_meta)} 个因子元数据")

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        thresholds = _load_thresholds(session, scene)
        latest_bt = _load_latest_backtests(session)

        for factor_id, rec in latest_bt.items():
            passed = _judge_pass(rec, thresholds)

            logger.info(
                f"因子 {factor_id} 判定结果: {'PASS' if passed else 'FAIL'}, "
                f"IC={rec.get('ic_value')}, IC_IR={rec.get('ic_ir')}, "
                f"Sharpe={rec.get('sharpe_ratio')}, MaxDD={rec.get('max_drawdown')}, "
                f"Turnover={rec.get('turnover')}"
            )

            # 1) 更新 factor_backtest.pass_standard
            update_bt_sql = text(
                """
                UPDATE factor_backtest
                SET pass_standard = :pass_standard
                WHERE id = :id
                """
            )
            session.execute(
                update_bt_sql,
                {
                    "pass_standard": passed,
                    "id": rec["id"],
                },
            )

            # 2) 更新 factor_basic.is_valid
            update_basic_sql = text(
                """
                UPDATE factor_basic
                SET is_valid = :is_valid
                WHERE factor_id = :factor_id
                """
            )
            session.execute(
                update_basic_sql,
                {
                    "is_valid": passed,
                    "factor_id": factor_id,
                },
            )

            # 3) 更新 factor_files
            fd = factor_meta.get(factor_id)
            if fd:
                # 存相对路径，避免把本机绝对路径写进 DB
                project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
                doc_path = os.path.relpath(fd.doc_path, start=project_root)
            else:
                doc_path = None
            json_path = os.path.join(backtest_results_dir, f"{factor_id}_backtest.json")
            if not os.path.exists(json_path):
                logger.warning(f"未找到回测 JSON 文件: {json_path}")
                json_path = None

            _upsert_factor_files(
                session=session,
                factor_id=factor_id,
                doc_path=doc_path,
                backtest_json_path=json_path,
            )

        session.commit()
        logger.info("selection_and_store 执行完成并已提交")
    except Exception as e:
        session.rollback()
        logger.error(f"selection_and_store 执行失败，已回滚: {e}")
    finally:
        session.close()


def main():
    run_selection_and_store()


if __name__ == "__main__":
    main()

