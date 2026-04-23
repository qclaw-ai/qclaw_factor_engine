# -*- coding: utf-8 -*-
"""
FastAPI 应用：POST 创建 job、GET 按 job_id 查询。

启动：仓库根 `python run_pipeline_job_api.py --config config.ini`（见该脚本）。
或 `cd src && set PYTHONPATH=. && python -m pipeline_job_api`（Windows 自辨）。
"""

from __future__ import annotations

import os
import logging
from typing import Annotated, Optional
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.engine import Engine

from common.config import PROJECT_ROOT
from common.db import get_db_manager
from pipeline_job_api import job_store
from pipeline_job_api.schemas import (
    ErrorBody,
    ErrorResponse,
    JobBacktestResultItem,
    JobCreateRequest,
    JobOut,
    JobResultResponse,
    RunMode,
)

logger = logging.getLogger(__name__)

# 在 uvicorn 下根 logger 常为 WARNING，子 logger 的 INFO 会被过滤；为 pipeline_job_api 树显式加控制台
_logging_configured = False


def _configure_pipeline_job_api_stream_logging() -> None:
    global _logging_configured
    if _logging_configured:
        return
    root = logging.getLogger("pipeline_job_api")
    h = logging.StreamHandler()
    h.setLevel(logging.INFO)
    h.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.setLevel(logging.INFO)
    root.addHandler(h)
    # 仅走本组 handler，避免与 uvicorn 根 handler 的级别冲突导致重复/丢失
    root.propagate = False
    _logging_configured = True


def resolve_config_path() -> str:
    p = os.environ.get("PIPELINE_JOB_API_CONFIG", "config.ini")
    if os.path.isabs(p):
        return p
    return os.path.join(PROJECT_ROOT, p)


def get_engine() -> Engine:
    """依赖注入：数据库不可用返回 503。"""
    try:
        return get_db_manager(config_file=resolve_config_path()).get_engine()
    except Exception as e:
        logger.exception("无法连接数据库")
        body = ErrorResponse(
            error=ErrorBody(
                code="DATABASE_UNAVAILABLE",
                message=str(e),
            )
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=body.model_dump(),
        ) from e


def _allow_full_run(
    # FastAPI：可选 Header 的默认值写在 = None，不能写在 Header(default=...) 里
    x_allow_full_run: Annotated[Optional[str], Header(alias="X-Allow-Full-Run")] = None,
) -> bool:
    if x_allow_full_run and str(x_allow_full_run).strip() == "1":
        return True
    if (os.environ.get("ALLOW_FULL") or "").strip() == "1":
        return True
    return False


def create_app() -> FastAPI:
    _configure_pipeline_job_api_stream_logging()

    app = FastAPI(
        title="qclaw_factor_engine pipeline job API",
        version="0.1.0",
        description="P1 阶段 C：factor_pipeline_job 写读接口（D 阶段 worker 可共用库表）。",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get(
        "/health",
        tags=["health"],
        summary="存活探针",
    )
    def health_live() -> dict:
        """不连库，供负载均衡 / K8s liveness 使用。"""
        return {"status": "ok", "service": "pipeline_job_api"}

    @app.get(
        "/api/v1/ready",
        tags=["health"],
        summary="就绪探针",
    )
    def health_ready(
        engine: Annotated[Engine, Depends(get_engine)],
    ) -> dict:
        """连库 SELECT 1；失败时与 get_engine 一致返回 503。"""
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "reachable"}

    @app.post(
        "/api/v1/pipeline/jobs",
        response_model=JobOut,
        status_code=status.HTTP_200_OK,
        summary="创建管线任务",
    )
    def post_create_job(
        body: JobCreateRequest,
        engine: Annotated[Engine, Depends(get_engine)],
        allow: Annotated[bool, Depends(_allow_full_run)] = False,
    ) -> JobOut:
        # C1：quick 为 true 时强制 run_mode=quick，且不触发 full 的防护
        if body.quick:
            effective_run: RunMode = "quick"
        else:
            if body.run_mode == "full" and not allow:
                raise _err(
                    status.HTTP_403_FORBIDDEN,
                    "FORBIDDEN_FULL_RUN",
                    "run_mode=full 需请求头 X-Allow-Full-Run: 1 或环境变量 ALLOW_FULL=1（见 P1 阶段 D4）",
                )
            effective_run = body.run_mode
        if effective_run == "selection_only":
            if body.backtest_job_id is None:
                raise _err(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "BACKTEST_JOB_ID_REQUIRED",
                    "run_mode=selection_only 时必须传 backtest_job_id",
                )
            source_job = job_store.get_job_by_public_id(engine, body.backtest_job_id)
            if source_job is None:
                raise _err(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "BACKTEST_JOB_NOT_FOUND",
                    f"backtest_job_id={body.backtest_job_id} 不存在",
                )
            if source_job.status != "success":
                raise _err(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "BACKTEST_JOB_NOT_SUCCESS",
                    f"backtest_job_id={body.backtest_job_id} 状态非 success，当前={source_job.status}",
                )
            if source_job.run_mode in ("quick", "trial", "selection_only"):
                raise _err(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "BACKTEST_JOB_RUN_MODE_FORBIDDEN",
                    "selection_only 仅允许引用 new_only/revalidate/full 的成功任务，禁止 quick/trial/selection_only",
                )
        try:
            missing = job_store._fetch_missing_factor_ids(
                engine, list(body.factor_ids)
            )
        except Exception as e:
            raise _err(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "DATABASE_ERROR",
                f"校验 factor_basic 失败: {e}",
            ) from e
        if missing:
            raise _err(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "FACTOR_NOT_IN_BASIC",
                "以下 factor_id 在 factor_basic 中不存在",
                details={"missing_factor_ids": missing},
            )
        out, _ = job_store.create_job_queued(
            engine,
            factor_ids=list(body.factor_ids),
            source_type=body.source_type,
            run_mode=effective_run,
            test_universe=body.test_universe,
            idempotency_key=body.idempotency_key,
            backtest_job_id=(str(body.backtest_job_id) if body.backtest_job_id is not None else None),
        )
        return out

    @app.get(
        "/api/v1/pipeline/jobs/{job_id}",
        response_model=JobOut,
        summary="查询任务",
    )
    def get_job(
        job_id: Annotated[UUID, Path(..., description="创建任务时返回的 job_id（即 public_id）")],
        engine: Annotated[Engine, Depends(get_engine)],
    ) -> JobOut:
        row = job_store.get_job_by_public_id(engine, job_id)
        if row is None:
            raise _err(
                status.HTTP_404_NOT_FOUND,
                "JOB_NOT_FOUND",
                f"未找到 public_id={job_id} 的任务",
            )
        return row

    @app.get(
        "/api/v1/pipeline/jobs/{job_id}/result",
        response_model=JobResultResponse,
        summary="查询该 job 产出的回测结果",
    )
    def get_job_result(
        job_id: Annotated[UUID, Path(..., description="创建任务时返回的 job_id（即 public_id）")],
        engine: Annotated[Engine, Depends(get_engine)],
    ) -> JobResultResponse:
        row = job_store.get_job_by_public_id(engine, job_id)
        if row is None:
            raise _err(
                status.HTTP_404_NOT_FOUND,
                "JOB_NOT_FOUND",
                f"未找到 public_id={job_id} 的任务",
            )
        items_raw = job_store.list_job_backtest_results(engine, job_id)
        items = [JobBacktestResultItem(**x) for x in items_raw]
        ready = row.status == "success"
        return JobResultResponse(
            job_id=row.job_id,
            status=row.status,
            ready=ready,
            items=items,
        )

    @app.exception_handler(HTTPException)
    def http_error_shape(_request: Request, ex: HTTPException) -> JSONResponse:
        d = ex.detail
        if isinstance(d, dict) and "error" in d:
            return JSONResponse(status_code=ex.status_code, content=d)
        return JSONResponse(
            status_code=ex.status_code,
            content=ErrorResponse(
                error=ErrorBody(code="HTTP", message=str(d))
            ).model_dump(),
        )

    return app


def _err(
    status_code: int,
    code: str,
    message: str,
    details: Optional[dict] = None,
) -> HTTPException:
    body = ErrorResponse(
        error=ErrorBody(code=code, message=message, details=details)
    )
    return HTTPException(
        status_code=status_code,
        detail=body.model_dump(),
    )


app = create_app()
