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
from pathlib import Path
from backtest_core.backtest_core_runner import run_backtest, BacktestResult
from common.config import Config
from common.db import get_db_manager
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


def _insert_factor_backtest(session, res: BacktestResult, result_json_rel_path: str | None) -> None:
    """将回测结果插入 factor_backtest 表（含实证域与 JSON 相对路径）"""
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
        """
    )

    session.execute(
        insert_sql,
        {
            "factor_id": res.factor_id,
            "test_universe": res.test_universe,
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
    )



def _upsert_factor_values_path(
    session,
    factor_id: str,
    factor_output_dir: str,
    universe: str,
) -> None:
    """
    在 factor_output_dir/by_universe/{UNIVERSE} 中查找以 <factor_id>_<UNIVERSE>_ 开头的 CSV 文件，
    用找到的真实文件名写入 factor_files.factor_values_path。
    """
    u_tag = _safe_universe_file_tag(universe).upper()
    root_dir = Path(factor_output_dir) / "by_universe" / u_tag
    pattern = f"{factor_id}_{u_tag}_*.csv"
    matches = list(root_dir.glob(pattern))
    if not matches:
        # 找不到就直接返回，不影响主流程
        logger.warning(
            "未在 %s 下找到因子 %s（universe=%s）的 CSV 文件",
            root_dir,
            factor_id,
            u_tag,
        )
        return

    # 如果有多个，按文件名排序取最新一个
    matches.sort()
    csv_path = matches[-1]

    # 计算相对项目根目录路径
    project_root = Path(__file__).resolve().parents[2]
    try:
        rel_path = csv_path.resolve().relative_to(project_root).as_posix()
    except ValueError:
        rel_path = csv_path.as_posix()

    session.execute(
        text(
            """
            INSERT INTO factor_files (factor_id, doc_path, factor_values_path)
            VALUES (:factor_id, '', :factor_values_path)
            ON CONFLICT (factor_id) DO UPDATE
            SET factor_values_path = EXCLUDED.factor_values_path
            """
        ),
        {"factor_id": factor_id, "factor_values_path": rel_path},
    )

def _write_backtest_json(
    base_dir: str,
    res: BacktestResult,
    meta: Dict[str, FactorDefinition],
) -> str:
    """将单个因子回测结果写入 JSON，返回绝对路径（按实证域分目录，避免混放）。"""
    u_tag = _safe_universe_file_tag(res.test_universe)
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
        "test_universe": res.test_universe,
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
    io_config_file: str = "src/backtest_io/config.ini",
    core_config_file: str = "src/backtest_core/config.ini",
) -> None:
    logger.info("启动 backtest_io_runner")

    cfg = Config(config_file=io_config_file)
    backtest_results_dir = cfg.get(
        "paths",
        "backtest_results_dir",
        fallback="backtest_results",
    )
    
    core_cfg = Config(config_file=core_config_file)
    factor_output_dir = core_cfg.get(
        "backtest",
        "factor_output_dir",
        fallback="factor_values",
    ).strip()

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

            project_root = Path(__file__).resolve().parents[2]
            try:
                json_rel = Path(json_path).resolve().relative_to(project_root).as_posix()
            except ValueError:
                json_rel = Path(json_path).as_posix()

            # 2) 确保 factor_basic 中有记录
            _ensure_factor_basic(session, factor_meta, res.factor_id)

            # 3) 插入 factor_backtest
            _insert_factor_backtest(session, res, result_json_rel_path=json_rel)
            
            # 4) 更新 factor_files.factor_values_path
            _upsert_factor_values_path(
                session=session,
                factor_id=res.factor_id,
                factor_output_dir=factor_output_dir,
                universe=res.test_universe,
            )

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

