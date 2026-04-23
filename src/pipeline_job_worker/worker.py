# -*- coding: utf-8 -*-
"""
单 worker：claim → run_factor_engine → run_backtest_io → 更新终态。

约定：同一条 job 内 ``factor_engine.universe`` 与 ``backtest.test_universe`` 使用同一解析结果（见 resolve）。
"""

from __future__ import annotations

import os
import sys

# 允许从 `src/pipeline_job_worker/worker.py` 直跑时找到 `common`（与 factor_engine 等一致）
_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import logging
import time
import traceback
from typing import Optional
from uuid import UUID

from common.config import Config
from common.db import get_db_manager
from common.universe_service import normalize_universe_code
from factor_engine.factor_engine_runner import run_factor_engine
from backtest_io.backtest_io_runner import run_backtest_io
from selection_and_store.selection_and_store_runner import run_selection_and_store
from pipeline_job_api import job_store
from pipeline_job_api.schemas import JobOut

logger = logging.getLogger("pipeline_job_worker")


def resolve_config_path(config_file: str) -> str:
    if os.path.isabs(config_file):
        return config_file
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(root, config_file)


def resolve_universe_for_job(job: JobOut, config_file: str) -> str:
    """与 P1 约定一致：job.test_universe 非空优先，否则根配置 [backtest] / [factor_engine]。"""
    if job.test_universe and str(job.test_universe).strip():
        return normalize_universe_code(str(job.test_universe).strip())
    cfg = Config(config_file=config_file)
    u_bt = (cfg.get("backtest", "test_universe", fallback="") or "").strip()
    u_fe = (cfg.get("factor_engine", "universe", fallback="") or "").strip()
    raw = u_bt or u_fe or "ALL"
    return normalize_universe_code(raw)


def _run_mode_supported(run_mode: str) -> tuple[bool, Optional[str]]:
    """(可跑, 若不跑则错误说明)。"""
    if run_mode == "full":
        if (os.environ.get("ALLOW_FULL") or "").strip() != "1":
            return (
                False,
                "run_mode=full 需环境变量 ALLOW_FULL=1（与 API 中 X-Allow-Full-Run 语义一致）",
            )
    return True, None


def _resolve_stage_by_run_mode(run_mode: str) -> str:
    """
    前端任务与增量主链隔离：按 run_mode 映射 factor_value_files.stage。

    - quick/trial -> trial
    - new_only/revalidate -> candidate
    - full -> production（full 的权限由 _run_mode_supported 护栏控制）
    """
    rm = (run_mode or "").strip().lower()
    if rm in ("quick", "trial"):
        return "trial"
    if rm in ("new_only", "revalidate"):
        return "candidate"
    if rm == "full":
        return "production"
    # 兜底：对未覆盖模式默认 candidate，避免误入 production。
    return "candidate"


def run_one_job(config_file: str, job: JobOut) -> dict:
    cfg_path = resolve_config_path(config_file)
    u = resolve_universe_for_job(job, cfg_path)
    fids = list(job.factor_ids)
    if not fids:
        raise ValueError("任务 factor_ids 为空，无法执行")

    ok, err = _run_mode_supported(job.run_mode)
    if not ok:
        raise RuntimeError(err or "run_mode 不支持")
    resolved_stage = _resolve_stage_by_run_mode(job.run_mode)

    logger.info(
        "执行 job: job_id=%s run_mode=%s source_type=%s universe_resolved=%s stage_resolved=%s factor_ids=%s",
        job.job_id,
        job.run_mode,
        job.source_type,
        u,
        resolved_stage,
        fids,
    )

    if job.run_mode == "selection_only":
        if job.backtest_job_id is None:
            raise RuntimeError("selection_only 缺少 backtest_job_id，拒绝执行")
        source_job = job_store.get_job_by_public_id(
            get_db_manager(config_file=cfg_path).get_engine(),
            job.backtest_job_id,
        )
        if source_job is None:
            raise RuntimeError(f"selection_only 来源任务不存在: {job.backtest_job_id}")
        if source_job.status != "success":
            raise RuntimeError(
                f"selection_only 来源任务状态非 success: job_id={job.backtest_job_id} status={source_job.status}"
            )
        if source_job.run_mode in ("quick", "trial", "selection_only"):
            raise RuntimeError(
                "selection_only 仅允许引用 new_only/revalidate/full 的成功任务，禁止 quick/trial/selection_only"
            )
        t_sel = time.perf_counter()
        logger.info("【selection_only】开始 run_selection_and_store, config_file=%s", cfg_path)
        selection_summary = run_selection_and_store(
            config_file=cfg_path,
            factor_ids_override=fids,
            test_universe_override=u,
            backtest_job_public_id=str(job.backtest_job_id),
        )
        logger.info(
            "【selection_only】run_selection_and_store 完成, 用时 %.1fs summary=%s",
            time.perf_counter() - t_sel,
            selection_summary,
        )
        return {
            "mode": "selection_only",
            "selection_summary": selection_summary,
            "created_backtests": [],
        }

    t0 = time.perf_counter()
    logger.info("【1/2】开始 run_factor_engine, config_file=%s stage_override=%s", cfg_path, resolved_stage)
    run_factor_engine(
        config_file=cfg_path,
        factor_ids_override=fids,
        universe_override=u,
        stage_override=resolved_stage,
    )
    logger.info(
        "【1/2】run_factor_engine 已正常返回, 用时 %.1fs (详见 logs/factor_engine_runner*.log)",
        time.perf_counter() - t0,
    )

    t1 = time.perf_counter()
    logger.info("【2/2】开始 run_backtest_io, io+core config=%s", cfg_path)
    created_backtests = run_backtest_io(
        io_config_file=cfg_path,
        core_config_file=cfg_path,
        factor_ids_override=fids,
        test_universe_override=u,
    )
    logger.info(
        "【2/2】run_backtest_io 已正常返回, 用时 %.1fs (详见 logs/backtest_io_runner*.log)",
        time.perf_counter() - t1,
    )
    logger.info("run_backtest_io 返回结果条数=%s", len(created_backtests))
    return {
        "mode": "compute_and_backtest",
        "created_backtests": created_backtests,
    }


def process_next(config_file: str, running_timeout_minutes: int = 30) -> bool:
    """
    领取并执行最多一条任务。
    :return: 是否处理了一条（含失败终态）
    """
    cfg_path = resolve_config_path(config_file)
    logger.info("======== process_next 开始, 根 config=%s ========", cfg_path)

    db = get_db_manager(config_file=cfg_path)
    engine = db.get_engine()
    # 异常退出后可能残留 running；每轮先按阈值回收到 queued。
    job_store.recover_timeout_running_jobs(engine, running_timeout_minutes)

    job = job_store.claim_next_queued(engine)
    if job is None:
        logger.info("无 queued 任务, 本轮回合结束 (若 --loop 将休眠后继续)")
        return False

    pid: UUID = job.job_id
    logger.info(
        "已领取任务: job_id=%s status=%s run_mode=%s test_universe(原始)=%s idempotency_key=%s",
        pid,
        job.status,
        job.run_mode,
        job.test_universe,
        job.idempotency_key,
    )
    t_all = time.perf_counter()
    try:
        run_output = run_one_job(config_file, job)
        created_rows = list(run_output.get("created_backtests") or [])
        linked = job_store.bind_job_backtest_rows(engine, pid, created_rows)
        u_final = resolve_universe_for_job(job, cfg_path)
        selection_summary = run_output.get("selection_summary")
        job_store.mark_job_success(
            engine,
            pid,
            {
                "stages_completed": (
                    ["selection_and_store"]
                    if job.run_mode == "selection_only"
                    else ["factor_engine", "backtest_io"]
                ),
                "run_mode": job.run_mode,
                "test_universe": u_final,
                "factor_ids": list(job.factor_ids),
                "factor_backtest_count": len(created_rows),
                "linked_backtest_count": linked,
                "selection_summary": selection_summary,
            },
        )
        logger.info(
            "======== 成功: 已回写库 status=success, job_id=%s, 总用时 %.1fs ========",
            pid,
            time.perf_counter() - t_all,
        )
    except Exception as e:
        tb = traceback.format_exc()
        msg = f"{e!s}\n\n{tb}"
        logger.exception("======== 任务执行异常 job_id=%s (见下方堆栈) ========", pid)
        try:
            job_store.mark_job_failed(engine, pid, msg)
            logger.error(
                "已回写库 status=failed, job_id=%s, 总用时 %.1fs",
                pid,
                time.perf_counter() - t_all,
            )
        except Exception as e2:
            logger.error("回写 failed 状态也失败, job_id=%s: %s", pid, e2)
    return True


def run_loop(config_file: str, interval_sec: float, running_timeout_minutes: int = 30) -> None:
    logger.info(
        "进入轮询: interval_sec=%.1f, running_timeout_minutes=%s, 用 Ctrl+C 结束",
        interval_sec,
        running_timeout_minutes,
    )
    while True:
        processed = process_next(config_file, running_timeout_minutes=running_timeout_minutes)
        if not processed:
            logger.info("sleep %.1fs 后再探询 queued 任务", interval_sec)
            time.sleep(interval_sec)


if __name__ == "__main__":
    # 与仓库根 `run_pipeline_job_worker.py` 等效（推荐仍用该脚本，便于 `setup_logger` 落盘）
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(description="pipeline job worker（直跑本文件时请传 --config，cwd 含 repo 根时相对 config 有效）")
    p.add_argument("--config", default="config.ini", help="根 ini 路径")
    p.add_argument("--once", action="store_true", help="只跑一轮")
    p.add_argument("--loop", action="store_true", help="无任务时休眠后持续轮询")
    p.add_argument("--interval", type=float, default=30.0, help="--loop 时休眠秒数")
    p.add_argument(
        "--running-timeout-minutes",
        type=int,
        default=30,
        help="running 超时回收阈值（分钟），默认 30",
    )
    a = p.parse_args()
    if a.once and a.loop:
        raise SystemExit("--once 与 --loop 二选一或都不写（默认等价 --once）")
    if not a.once and not a.loop:
        a.once = True
    if a.loop:
        run_loop(
            a.config,
            a.interval,
            running_timeout_minutes=a.running_timeout_minutes,
        )
    else:
        process_next(a.config, running_timeout_minutes=a.running_timeout_minutes)
