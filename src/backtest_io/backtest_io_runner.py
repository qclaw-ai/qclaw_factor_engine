#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

# 把 common / backtest_core / factor_docs 加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from pathlib import Path
from backtest_core.backtest_core_runner import run_backtest, BacktestResult
from common.config import Config
from common.db import get_db_manager
from common.universe_service import normalize_universe_code
from common.utils import setup_logger
from factor_docs.factor_docs_parser import load_all_factors, FactorDefinition

logger = setup_logger("backtest_io_runner", "logs/backtest_io_runner.log")


def _safe_universe_file_tag(test_universe: str) -> str:
    """文件名安全片段：去掉路径分隔符等。"""
    s = (test_universe or "ALL").strip()
    for ch in '\\/:*?"<>|':
        s = s.replace(ch, "_")
    return s or "ALL"


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


def _insert_factor_backtest(session, res: BacktestResult, result_json_rel_path: str | None) -> int:
    """将回测结果插入 factor_backtest 表（含实证域与 JSON 相对路径），返回新行 id。"""
    insert_sql = text(
        """
        INSERT INTO factor_backtest (
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
            result_json_rel_path,
            comment
        ) VALUES (
            :factor_id,
            :test_universe,
            :backtest_period,
            :horizon,
            :ic_value,
            :ic_ir,
            :sharpe_ratio,
            :max_drawdown,
            :turnover,
            :pass_standard,
            :result_json_rel_path,
            :comment
        )
        RETURNING id
        """
    )

    row = session.execute(
        insert_sql,
        {
            "factor_id": res.factor_id,
            "test_universe": normalize_universe_code(res.test_universe),
            "backtest_period": res.backtest_period,
            "horizon": res.horizon,
            "ic_value": res.ic_value,
            "ic_ir": res.ic_ir,
            "sharpe_ratio": res.sharpe_ratio,
            "max_drawdown": res.max_drawdown,
            "turnover": res.turnover,
            "pass_standard": None,  # 是否通过标准由 selection_and_store 再更新
            "result_json_rel_path": result_json_rel_path,
            "comment": None,
        },
    ).fetchone()
    if row is None:
        raise RuntimeError(f"插入 factor_backtest 失败（无 RETURNING），factor_id={res.factor_id}")
    return int(row[0])

def _write_backtest_json(
    base_dir: str,
    res: BacktestResult,
    meta: Dict[str, FactorDefinition],
) -> str:
    """将单个因子回测结果写入 JSON，返回绝对路径（按实证域分目录，避免混放）。"""
    # 与磁盘 by_universe 目录及 factor_value_files.universe 对齐（含 ALL_A -> ALL）
    u_norm = normalize_universe_code(res.test_universe)
    u_tag = _safe_universe_file_tag(u_norm)
    # 与 factor_values 保持一致：按域分目录（包含 ALL）
    universe_dir = os.path.join(base_dir, "by_universe", u_tag)
    os.makedirs(universe_dir, exist_ok=True)
    file_name = f"{res.factor_id}_{u_tag}_backtest.json"
    path = os.path.join(universe_dir, file_name)

    fd = meta.get(res.factor_id)

    payload = {
        "factor_id": res.factor_id,
        "factor_name": fd.factor_name if fd else res.factor_id,
        "factor_type": fd.factor_type if fd else None,
        # 实证域以本次大回测为准（与 md 中适用股票池可并存）
        "test_universe": u_norm,
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
    io_config_file: str = "config.ini",
    core_config_file: str = "config.ini",
    *,
    factor_ids_override: Optional[Sequence[str]] = None,
    test_universe_override: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    回测结果落盘 JSON + 写 ``factor_backtest`` / 确保 ``factor_basic``。

    与 P1 对接时通过 ``factor_ids_override`` / ``test_universe_override`` 与单条 job 对齐；
    两参数为 None 时，行为与仅传 ini 的 ``run_backtest`` 一致（见 ``backtest_core``）。
    """
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
    results = run_backtest(
        config_file=core_config_file,
        factor_ids_override=factor_ids_override,
        test_universe_override=test_universe_override,
    )
    if not results:
        logger.warning("未获得任何回测结果，结束 backtest_io")
        return []

    db_manager = get_db_manager(config_file=io_config_file)
    session = db_manager.get_session()

    created_rows: List[Dict[str, Any]] = []
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

            project_root = Path(__file__).resolve().parents[2]
            try:
                json_rel = Path(json_path).resolve().relative_to(project_root).as_posix()
            except ValueError:
                json_rel = Path(json_path).as_posix()

            # 2) 确保 factor_basic 中有记录
            _ensure_factor_basic(session, factor_meta, res.factor_id)

            # 3) 插入 factor_backtest
            # 批量因子值路径以 factor_value_files 为准（因子引擎写入），不由本任务按回测域覆盖 factor_values_path。
            inserted_id = _insert_factor_backtest(session, res, result_json_rel_path=json_rel)
            created_rows.append(
                {
                    "factor_backtest_id": inserted_id,
                    "factor_id": res.factor_id,
                    "test_universe": normalize_universe_code(res.test_universe),
                    "result_json_rel_path": json_rel,
                }
            )

        session.commit()
        logger.info("backtest_io 全部写入 DB 成功，新增 factor_backtest 行数=%s", len(created_rows))
        return created_rows
    except Exception as e:
        session.rollback()
        logger.error(f"backtest_io 执行失败，已回滚: {e}")
        raise
    finally:
        session.close()


def main() -> None:
    p = argparse.ArgumentParser(description="回测落库与 JSON 写入（io 层）")
    p.add_argument(
        "--io-config",
        default="config.ini",
        help="io 用根配置（paths/backtest 结果目录、DB 等），非 prod 自动 *_dev.ini",
    )
    p.add_argument(
        "--core-config",
        default="config.ini",
        help="传给 backtest_core 的根配置，可与 io-config 相同",
    )
    p.add_argument(
        "--factor-ids",
        default=None,
        help="覆写 [backtest].factor_ids，逗号分隔；与 run_backtest(factor_ids_override=...) 一致",
    )
    p.add_argument(
        "--test-universe",
        default=None,
        help="覆写 [backtest].test_universe；与 run_backtest(test_universe_override=...) 一致",
    )
    args = p.parse_args()

    f_arg: Optional[List[str]] = None
    if args.factor_ids is not None:
        f_arg = [x.strip() for x in args.factor_ids.split(",") if x.strip()]
        if not f_arg:
            raise SystemExit("错误：--factor-ids 去空白后为空，或不要传此参数以使用配置文件")

    u_arg: Optional[str] = None
    if args.test_universe is not None:
        s = str(args.test_universe).strip()
        if not s:
            raise SystemExit("错误：--test-universe 不能为全空白，或不要传此参数以使用配置文件")
        u_arg = s

    run_backtest_io(
        io_config_file=args.io_config,
        core_config_file=args.core_config,
        factor_ids_override=f_arg,
        test_universe_override=u_arg,
    )


if __name__ == "__main__":
    main()

