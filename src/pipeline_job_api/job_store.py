# -*- coding: utf-8 -*-
"""
factor_pipeline_job 表：插入 + 冪等查询 + 按 public_id 查询。

冪等规则见 docs/因子工厂_P1_新增因子入库与回测_详细步骤.md「阶段 A 定稿」。
依赖 sql/migrations/005 中部分唯一索引 uq_factor_pipeline_job_idempotency_key_active（同 key 在 queued/running 至多一行）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from pipeline_job_api.schemas import JobOut

logger = logging.getLogger(__name__)


def _normalize_factor_id_list(factor_ids: List[str]) -> str:
    out: List[str] = []
    seen = set()
    for x in factor_ids:
        s = (x or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    if not out:
        raise ValueError("factor_ids 为空或全部空白")
    return ",".join(out)


def _fetch_missing_factor_ids(
    engine: Engine,
    factor_ids: List[str],
) -> List[str]:
    if not factor_ids:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT factor_id
                FROM factor_basic
                WHERE factor_id = ANY(:factor_ids)
                """
            ),
            {"factor_ids": list(factor_ids)},
        ).fetchall()
    found = {str(r[0]).strip() for r in rows if r[0] is not None}
    return [f for f in factor_ids if f not in found]


def create_job_queued(
    engine: Engine,
    *,
    factor_ids: List[str],
    source_type: str,
    run_mode: str,
    test_universe: Optional[str],
    idempotency_key: Optional[str],
    backtest_job_id: Optional[str] = None,
) -> Tuple[JobOut, bool]:
    """
    插入 queued 行，或返回同 idempotency_key 下已存在的 queued/running 行（第二项 idempotent_replay=True）。
    """
    store_text = _normalize_factor_id_list(factor_ids)
    tu: Optional[str] = None
    if test_universe is not None and str(test_universe).strip() != "":
        tu = str(test_universe).strip()
    ik = idempotency_key
    if ik is not None and str(ik).strip() == "":
        ik = None
    if ik is not None:
        ik = str(ik).strip()[:128]
    bjid: Optional[str] = None
    if backtest_job_id is not None and str(backtest_job_id).strip() != "":
        bjid = str(backtest_job_id).strip()

    logger.info(
        "create_job_queued: 请求 run_mode=%s source_type=%s factor_count=%s test_universe=%s idempotency_key=%s backtest_job_id=%s",
        run_mode,
        source_type,
        len(factor_ids),
        test_universe,
        (ik if ik is not None else "<未传>"),
        (bjid if bjid is not None else "<未传>"),
    )

    insert_sql = text(
        """
        INSERT INTO factor_pipeline_job (
            status, source_type, run_mode, factor_ids, test_universe, idempotency_key, backtest_job_id
        ) VALUES (
            'queued', :source_type, :run_mode, :factor_ids, :test_universe, :idempotency_key, CAST(:backtest_job_id AS uuid)
        )
        RETURNING
            public_id, status, source_type, run_mode, factor_ids, test_universe, backtest_job_id, idempotency_key,
            error_message, result_summary, log_rel_path, created_at, started_at, finished_at
        """
    )

    select_active_sql = text(
        """
        SELECT
            public_id, status, source_type, run_mode, factor_ids, test_universe, backtest_job_id, idempotency_key,
            error_message, result_summary, log_rel_path, created_at, started_at, finished_at
        FROM factor_pipeline_job
        WHERE idempotency_key = :idempotency_key
          AND status IN ('queued', 'running')
        ORDER BY id DESC
        LIMIT 1
        """
    )

    if ik is not None:
        with engine.connect() as conn:
            row0 = conn.execute(
                select_active_sql, {"idempotency_key": ik}
            ).fetchone()
        if row0 is not None:
            j = JobOut.from_row(row0, idempotent_replay=True)
            logger.info(
                "create_job_queued: 冪等复用(queued/running) job_id=%s status=%s idempotency_key=%s",
                j.job_id,
                j.status,
                ik,
            )
            return j, True

    try:
        with engine.begin() as conn:
            row = conn.execute(
                insert_sql,
                {
                    "source_type": source_type,
                    "run_mode": run_mode,
                    "factor_ids": store_text,
                    "test_universe": tu,
                    "idempotency_key": ik,
                    "backtest_job_id": bjid,
                },
            ).fetchone()
    except IntegrityError as e:
        # 与并发 POST 争用同 idempotency_key：insert 后另一连接已建 active 行
        logger.warning(
            "create_job_queued: INSERT 发生 IntegrityError(冪等/并发) idempotency_key=%s err=%s",
            ik,
            e,
        )
        if ik is None:
            raise
        with engine.connect() as conn:
            row1 = conn.execute(
                select_active_sql, {"idempotency_key": ik}
            ).fetchone()
        if row1 is not None:
            j = JobOut.from_row(row1, idempotent_replay=True)
            logger.info(
                "create_job_queued: 冲突后重查命中 job_id=%s status=%s",
                j.job_id,
                j.status,
            )
            return j, True
        raise

    assert row is not None
    new_job = JobOut.from_row(row, idempotent_replay=False)
    logger.info(
        "create_job_queued: 新插入 job_id=%s status=queued run_mode=%s",
        new_job.job_id,
        new_job.run_mode,
    )
    return new_job, False


def get_job_by_public_id(engine: Engine, public_id) -> Optional[JobOut]:
    u = str(public_id)
    sql = text(
        """
        SELECT
            public_id, status, source_type, run_mode, factor_ids, test_universe, backtest_job_id, idempotency_key,
            error_message, result_summary, log_rel_path, created_at, started_at, finished_at
        FROM factor_pipeline_job
        WHERE public_id = CAST(:pid AS uuid)
        LIMIT 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql, {"pid": u}).fetchone()
    if row is None:
        logger.info("get_job_by_public_id: 未找到 public_id=%s", u)
        return None
    return JobOut.from_row(row, idempotent_replay=False)


def claim_next_queued(engine: Engine) -> Optional[JobOut]:
    """
    将最早一条 ``queued`` 置为 ``running`` 并设 ``started_at``（冪等上锁：FOR UPDATE SKIP LOCKED）。
    无可用任务时返回 None。
    """
    sql = text(
        """
        WITH picked AS (
            SELECT id
            FROM factor_pipeline_job
            WHERE status = 'queued'
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE factor_pipeline_job AS j
        SET
            status = 'running',
            started_at = COALESCE(j.started_at, now())
        FROM picked
        WHERE j.id = picked.id
        RETURNING
            j.public_id, j.status, j.source_type, j.run_mode, j.factor_ids, j.test_universe, j.backtest_job_id, j.idempotency_key,
            j.error_message, j.result_summary, j.log_rel_path, j.created_at, j.started_at, j.finished_at
        """
    )
    with engine.begin() as conn:
        row = conn.execute(sql).fetchone()
    if row is None:
        logger.info("claim_next_queued: 当前无可用 queued 行（或全部被其它 worker 锁定）")
        return None
    j = JobOut.from_row(row, idempotent_replay=False)
    nf = len(j.factor_ids)
    preview = j.factor_ids[:5] if nf > 5 else j.factor_ids
    more = f" (共{nf}个)" if nf > 5 else ""
    logger.info(
        "claim_next_queued: 已置为 running job_id=%s run_mode=%s factor_ids=%s%s",
        j.job_id,
        j.run_mode,
        preview,
        more,
    )
    return j


def mark_job_success(
    engine: Engine,
    public_id,
    result_summary: Dict[str, Any],
) -> None:
    rs_keys = list(result_summary.keys()) if isinstance(result_summary, dict) else "?"
    logger.info(
        "mark_job_success: 准备更新 public_id=%s result_summary_keys=%s",
        public_id,
        rs_keys,
    )
    with engine.begin() as conn:
        r = conn.execute(
            text(
                """
                UPDATE factor_pipeline_job
                SET
                    status = 'success',
                    finished_at = now(),
                    result_summary = CAST(:rs AS jsonb),
                    error_message = NULL
                WHERE public_id = CAST(:pid AS uuid)
                """
            ),
            {
                "rs": json.dumps(result_summary, ensure_ascii=False),
                "pid": str(public_id),
            },
        )
        n = r.rowcount if hasattr(r, "rowcount") else -1
    if n == 0:
        logger.error(
            "mark_job_success: UPDATE 影响 0 行，public_id 可能不存在或已终态: %s",
            public_id,
        )
    else:
        logger.info("mark_job_success: 已写 success, public_id=%s rowcount=%s", public_id, n)


def mark_job_failed(
    engine: Engine,
    public_id,
    error_message: str,
) -> None:
    msg = (error_message or "")[:20000]
    prev = (msg[:500].replace("\n", "\\n") if msg else "")
    logger.info(
        "mark_job_failed: 准备更新 public_id=%s err_len=%s preview=%s",
        public_id,
        len(msg),
        (prev + ("…" if len(msg) > 500 else "")),
    )

    with engine.begin() as conn:
        r = conn.execute(
            text(
                """
                UPDATE factor_pipeline_job
                SET
                    status = 'failed',
                    finished_at = now(),
                    error_message = :msg,
                    result_summary = NULL
                WHERE public_id = CAST(:pid AS uuid)
                """
            ),
            {"msg": msg, "pid": str(public_id)},
        )
        n = r.rowcount if hasattr(r, "rowcount") else -1

    if n == 0:
        logger.error(
            "mark_job_failed: UPDATE 影响 0 行，public_id 可能不存在: %s",
            public_id,
        )
    else:
        logger.info("mark_job_failed: 已写 failed, public_id=%s rowcount=%s", public_id, n)


def bind_job_backtest_rows(
    engine: Engine,
    public_id,
    rows: List[Dict[str, Any]],
) -> int:
    """
    批量写入 job 与 factor_backtest 的映射。
    rows 元素需含: factor_backtest_id, factor_id。
    """
    if not rows:
        logger.info("bind_job_backtest_rows: 无需写入映射 public_id=%s", public_id)
        return 0

    sql = text(
        """
        INSERT INTO factor_pipeline_job_backtest (
            job_public_id,
            factor_backtest_id,
            factor_id
        ) VALUES (
            CAST(:pid AS uuid),
            :factor_backtest_id,
            :factor_id
        )
        ON CONFLICT (job_public_id, factor_backtest_id) DO NOTHING
        """
    )
    payload = [
        {
            "pid": str(public_id),
            "factor_backtest_id": int(r["factor_backtest_id"]),
            "factor_id": str(r["factor_id"]),
        }
        for r in rows
    ]
    with engine.begin() as conn:
        r = conn.execute(sql, payload)
        n = r.rowcount if hasattr(r, "rowcount") else -1
    logger.info(
        "bind_job_backtest_rows: public_id=%s 入参=%s 实际插入(rowcount)=%s",
        public_id,
        len(payload),
        n,
    )
    return max(0, n if isinstance(n, int) else 0)


def list_job_backtest_results(engine: Engine, public_id) -> List[Dict[str, Any]]:
    """
    查询某 job 映射出的回测结果（严格按关联表）。
    """
    sql = text(
        """
        SELECT
            l.factor_backtest_id,
            l.factor_id,
            fb.test_universe,
            fb.backtest_period,
            fb.backtest_time,
            fb.horizon,
            fb.ic_value,
            fb.ic_ir,
            fb.sharpe_ratio,
            fb.max_drawdown,
            fb.turnover,
            fb.pass_standard,
            fb.result_json_rel_path,
            l.created_at
        FROM factor_pipeline_job_backtest l
        JOIN factor_backtest fb
          ON fb.id = l.factor_backtest_id
        WHERE l.job_public_id = CAST(:pid AS uuid)
        ORDER BY l.id ASC
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"pid": str(public_id)}).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        m = r._mapping if hasattr(r, "_mapping") else r
        d = dict(m) if not isinstance(m, dict) else m
        out.append(d)
    logger.info("list_job_backtest_results: public_id=%s rows=%s", public_id, len(out))
    return out


def recover_timeout_running_jobs(
    engine: Engine,
    timeout_minutes: int,
) -> int:
    """
    将超时 running 任务回收到 queued（started_at 早于 now - timeout_minutes）。

    :return: 回收条数
    """
    if timeout_minutes <= 0:
        logger.warning("recover_timeout_running_jobs: timeout_minutes=%s 非法，跳过", timeout_minutes)
        return 0
    sql = text(
        """
        UPDATE factor_pipeline_job
        SET
            status = 'queued',
            started_at = NULL,
            finished_at = NULL,
            error_message = NULL
        WHERE status = 'running'
          AND started_at IS NOT NULL
          AND started_at <= (now() - make_interval(mins => :timeout_minutes))
        """
    )
    with engine.begin() as conn:
        r = conn.execute(sql, {"timeout_minutes": int(timeout_minutes)})
        n = r.rowcount if hasattr(r, "rowcount") else -1
    n_int = max(0, n if isinstance(n, int) else 0)
    if n_int > 0:
        logger.warning(
            "recover_timeout_running_jobs: 已回收超时 running 任务=%s（阈值=%s 分钟）",
            n_int,
            timeout_minutes,
        )
    else:
        logger.info(
            "recover_timeout_running_jobs: 无超时 running（阈值=%s 分钟）",
            timeout_minutes,
        )
    return n_int
