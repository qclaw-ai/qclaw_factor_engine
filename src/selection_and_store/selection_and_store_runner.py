#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from typing import Dict, Any, List, Optional, Sequence

from decimal import InvalidOperation

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


def _load_latest_backtests_per_universe(session) -> list[Dict[str, Any]]:
    """每个 (factor_id, test_universe) 取最新一条 factor_backtest（多领域并存）。"""
    sql = text(
        """
        SELECT DISTINCT ON (factor_id, test_universe)
            id,
            factor_id,
            test_universe,
            backtest_period,
            horizon,
            ic_value,
            ic_ir,
            sharpe_ratio,
            max_drawdown,
            turnover,
            pass_standard,
            backtest_time,
            result_json_rel_path
        FROM factor_backtest
        ORDER BY factor_id, test_universe, backtest_time DESC, id DESC
        """
    )
    rows = session.execute(sql).mappings().all()
    out = [dict(r) for r in rows]
    logger.info("共加载到 %d 条 (因子, 实证域) 维度的最新回测记录", len(out))
    return out


def _load_backtests_by_job_public_id(session, job_public_id: str) -> list[Dict[str, Any]]:
    """仅加载指定 job 关联的回测结果，避免全局 latest 污染 selection。"""
    sql = text(
        """
        SELECT
            fb.id,
            fb.factor_id,
            fb.test_universe,
            fb.backtest_period,
            fb.horizon,
            fb.ic_value,
            fb.ic_ir,
            fb.sharpe_ratio,
            fb.max_drawdown,
            fb.turnover,
            fb.pass_standard,
            fb.backtest_time,
            fb.result_json_rel_path
        FROM factor_pipeline_job_backtest l
        JOIN factor_backtest fb
          ON fb.id = l.factor_backtest_id
        WHERE l.job_public_id = CAST(:pid AS uuid)
        ORDER BY l.id ASC
        """
    )
    rows = session.execute(sql, {"pid": str(job_public_id)}).mappings().all()
    out = [dict(r) for r in rows]
    logger.info(
        "按来源 job 读取回测结果 rows=%s backtest_job_id=%s",
        len(out),
        job_public_id,
    )
    return out


def _upsert_factor_universe_status(
    session,
    factor_id: str,
    test_universe: str,
    is_valid: bool,
) -> None:
    """按 (因子, 领域) 写入有效位；factor_basic.is_valid 由此派生。"""
    sql = text(
        """
        INSERT INTO factor_universe_status (
            factor_id,
            test_universe,
            is_valid,
            updated_at
        ) VALUES (
            :factor_id,
            :test_universe,
            :is_valid,
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (factor_id, test_universe) DO UPDATE SET
            is_valid = EXCLUDED.is_valid,
            updated_at = EXCLUDED.updated_at
        """
    )
    session.execute(
        sql,
        {
            "factor_id": factor_id,
            "test_universe": test_universe,
            "is_valid": is_valid,
        },
    )


def _sync_factor_basic_is_valid(session, factor_id: str) -> None:
    """任一侧为 TRUE 则 factor_basic.is_valid = TRUE（兼容日更等只读 is_valid 的逻辑）。"""
    sql = text(
        """
        UPDATE factor_basic
        SET is_valid = EXISTS (
            SELECT 1 FROM factor_universe_status fus
            WHERE fus.factor_id = :factor_id AND fus.is_valid = TRUE
        )
        WHERE factor_id = :factor_id
        """
    )
    session.execute(sql, {"factor_id": factor_id})


def _judge_pass(res: Dict[str, Any], th: Dict[str, Any]) -> bool:
    """根据阈值判断因子是否通过入库标准"""
    ic_value = res.get("ic_value")
    ic_ir = res.get("ic_ir")
    sharpe = res.get("sharpe_ratio")
    max_dd = res.get("max_drawdown")
    turnover = res.get("turnover")

    def _ge(x, y, field_name: str):
        if x is None or y is None:
            return False

        try:
            return x >= y
        except InvalidOperation:
            logger.warning(
                f"字段 {field_name} 比较异常，x={x}, y={y}，视为不通过阈值判断"
            )
            return False

    def _le(x, y, field_name: str):
        if x is None or y is None:
            return False

        try:
            return x <= y
        except InvalidOperation:
            logger.warning(
                f"字段 {field_name} 比较异常，x={x}, y={y}，视为不通过阈值判断"
            )
            return False

    # 只在阈值非空时启用该约束
    if th.get("ic_min") is not None and not _ge(ic_value, th["ic_min"], "ic_value"):
        return False
    if th.get("ic_ir_min") is not None and not _ge(ic_ir, th["ic_ir_min"], "ic_ir"):
        return False
    if th.get("sharpe_min") is not None and not _ge(sharpe, th["sharpe_min"], "sharpe_ratio"):
        return False
    # max_drawdown_max 为“最大允许回撤”（负数），回测结果需要 >= 该阈值
    if th.get("max_drawdown_max") is not None and not _ge(max_dd, th["max_drawdown_max"], "max_drawdown"):
        return False
    if th.get("turnover_max") is not None and not _le(turnover, th["turnover_max"], "turnover"):
        return False

    return True


def _upsert_factor_files(
    session,
    factor_id: str,
    doc_path: str | None,
) -> None:
    """更新 factor_files 的基础路径信息（若不存在则插入一条记录）。"""
    insert_sql = text(
        """
        INSERT INTO factor_files (
            factor_id,
            doc_path,
            log_path
        ) VALUES (
            :factor_id,
            :doc_path,
            :log_path
        )
        ON CONFLICT (factor_id) DO UPDATE SET
            doc_path = COALESCE(EXCLUDED.doc_path, factor_files.doc_path)
        """
    )

    session.execute(
        insert_sql,
        {
            "factor_id": factor_id,
            "doc_path": doc_path,
            "log_path": None,
        },
    )


def run_selection_and_store(
    config_file: str = "config.ini",
    *,
    factor_ids_override: Optional[Sequence[str]] = None,
    test_universe_override: Optional[str] = None,
    backtest_job_public_id: Optional[str] = None,
) -> Dict[str, Any]:
    logger.info("启动 selection_and_store_runner")

    cfg = Config(config_file=config_file)
    scene = cfg.get("selection", "scene", fallback="A_stock_daily_single_factor")
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    # 加载因子元数据（用于 doc_path / 名称等）
    factor_meta_list = load_all_factors()
    factor_meta: Dict[str, FactorDefinition] = {f.factor_id: f for f in factor_meta_list}
    logger.info(f"已加载 {len(factor_meta)} 个因子元数据")

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()
    selected_factor_ids: Optional[List[str]] = None
    if factor_ids_override is not None:
        selected_factor_ids = [str(x).strip() for x in factor_ids_override if str(x).strip()]
        selected_factor_ids = list(dict.fromkeys(selected_factor_ids))
        if not selected_factor_ids:
            raise RuntimeError("selection_only 收到空的 factor_ids_override")
        logger.info(
            "selection_only 仅处理指定因子 factor_count=%s factor_ids=%s",
            len(selected_factor_ids),
            selected_factor_ids,
        )

    try:
        thresholds = _load_thresholds(session, scene)
        if backtest_job_public_id is not None and str(backtest_job_public_id).strip():
            latest_rows = _load_backtests_by_job_public_id(
                session,
                str(backtest_job_public_id).strip(),
            )
        else:
            latest_rows = _load_latest_backtests_per_universe(session)
        if selected_factor_ids is not None:
            fid_set = set(selected_factor_ids)
            latest_rows = [r for r in latest_rows if r.get("factor_id") in fid_set]
        if test_universe_override is not None and str(test_universe_override).strip():
            u = str(test_universe_override).strip()
            latest_rows = [r for r in latest_rows if str(r.get("test_universe") or "ALL") == u]

        if not latest_rows:
            raise RuntimeError("selection_only 未找到可判定的 factor_backtest 最新记录")

        touched_factors: set[str] = set()

        pass_count = 0
        fail_count = 0
        for rec in latest_rows:
            factor_id = rec["factor_id"]
            test_u = rec.get("test_universe") or "ALL"
            touched_factors.add(factor_id)
            passed = _judge_pass(rec, thresholds)
            if passed:
                pass_count += 1
            else:
                fail_count += 1

            logger.info(
                "因子 %s 领域 %s 判定: %s, IC=%s, IC_IR=%s, Sharpe=%s, MaxDD=%s, Turnover=%s",
                factor_id,
                test_u,
                "PASS" if passed else "FAIL",
                rec.get("ic_value"),
                rec.get("ic_ir"),
                rec.get("sharpe_ratio"),
                rec.get("max_drawdown"),
                rec.get("turnover"),
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

            # 2) 按 (因子, 领域) 更新有效位
            _upsert_factor_universe_status(
                session,
                factor_id=factor_id,
                test_universe=test_u,
                is_valid=passed,
            )

        # 3) 派生 factor_basic.is_valid；4) factor_files（每个因子一次）
        for factor_id in touched_factors:
            _sync_factor_basic_is_valid(session, factor_id)

            fd = factor_meta.get(factor_id)
            if fd:
                doc_path = os.path.relpath(fd.doc_path, start=project_root).replace("\\", "/")
            else:
                doc_path = None

            _upsert_factor_files(
                session=session,
                factor_id=factor_id,
                doc_path=doc_path,
            )

        session.commit()
        summary = {
            "scene": scene,
            "backtest_job_id": backtest_job_public_id,
            "selected_factor_count": len(touched_factors),
            "pass_count": pass_count,
            "fail_count": fail_count,
        }
        logger.info("selection_and_store 执行完成并已提交 summary=%s", summary)
        return summary
    except Exception as e:
        session.rollback()
        logger.error(f"selection_and_store 执行失败，已回滚: {e}")
        raise
    finally:
        session.close()


def main():
    run_selection_and_store(
        config_file="config.ini"
    )


if __name__ == "__main__":
    main()

