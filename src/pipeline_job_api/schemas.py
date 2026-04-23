# -*- coding: utf-8 -*-
"""
请求 / 响应体（Pydantic），与 OpenAPI 自动生成一致。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

SourceType = Literal["crawl", "llm", "manual"]
RunMode = Literal["new_only", "full", "revalidate", "quick", "trial", "selection_only"]
JobStatus = Literal["queued", "running", "success", "failed"]


class JobCreateRequest(BaseModel):
    """C1：创建任务体"""

    factor_ids: List[str] = Field(..., min_length=1, description="因子 id 列表，至少一个")
    source_type: SourceType
    run_mode: RunMode
    # 与 P1 文档 C1 一致：可选 quick 标志；为 true 时强制 run_mode=quick
    quick: bool = False
    test_universe: Optional[str] = Field(
        default=None, description="实证域，空则表示使用根配置 [backtest].test_universe 等"
    )
    idempotency_key: Optional[str] = Field(
        default=None, max_length=128, description="冪等键，见 P1 阶段 A 定稿"
    )
    backtest_job_id: Optional[UUID] = Field(
        default=None,
        description="仅 run_mode=selection_only 使用：来源回测任务的 job_id（public_id）",
    )

    @field_validator("factor_ids", mode="before")
    @classmethod
    def strip_factor_ids(cls, v: object) -> object:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return v


class ErrorBody(BaseModel):
    """C3 统一错误体"""

    code: str
    message: str
    details: Optional[dict] = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class JobOut(BaseModel):
    """C2：查询/创建后返回的 job 表示（对外用 job_id = public_id）"""

    job_id: UUID
    status: JobStatus
    source_type: SourceType
    run_mode: RunMode
    factor_ids: List[str]
    test_universe: Optional[str] = None
    backtest_job_id: Optional[UUID] = None
    idempotency_key: Optional[str] = None
    error_message: Optional[str] = None
    result_summary: Optional[dict] = None
    log_rel_path: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # 冪等：与已有进行中的任务合并时为 True（HTTP 仍为 200）
    idempotent_replay: bool = False

    @staticmethod
    def _split_ids(text: str) -> List[str]:
        out: List[str] = []
        t = (text or "").strip()
        if not t:
            return out
        if t.startswith("["):
            try:
                arr = json.loads(t)
                if isinstance(arr, list):
                    return [str(x).strip() for x in arr if str(x).strip()]
            except (json.JSONDecodeError, TypeError):
                pass
        return [x.strip() for x in t.split(",") if x.strip()]

    @classmethod
    def from_row(
        cls,
        row: object,
        *,
        idempotent_replay: bool = False,
    ) -> "JobOut":
        m = row._mapping if hasattr(row, "_mapping") else row
        d = dict(m) if not isinstance(m, dict) else m
        raw_ids = d.get("factor_ids") or ""
        pid = d["public_id"]
        if not isinstance(pid, UUID):
            pid = UUID(str(pid))
        return cls(
            job_id=pid,
            status=d["status"],
            source_type=d["source_type"],
            run_mode=d["run_mode"],
            factor_ids=cls._split_ids(str(raw_ids)),
            test_universe=d.get("test_universe"),
            backtest_job_id=d.get("backtest_job_id"),
            idempotency_key=d.get("idempotency_key"),
            error_message=d.get("error_message"),
            result_summary=d.get("result_summary"),
            log_rel_path=d.get("log_rel_path"),
            created_at=d["created_at"],
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            idempotent_replay=idempotent_replay,
        )


class JobBacktestResultItem(BaseModel):
    factor_backtest_id: int
    factor_id: str
    test_universe: Optional[str] = None
    backtest_period: Optional[str] = None
    backtest_time: Optional[datetime] = None
    horizon: Optional[str] = None
    ic_value: Optional[float] = None
    ic_ir: Optional[float] = None
    sharpe_ratio: Optional[float] = None
    max_drawdown: Optional[float] = None
    turnover: Optional[float] = None
    pass_standard: Optional[bool] = None
    result_json_rel_path: Optional[str] = None
    created_at: Optional[datetime] = None


class JobResultResponse(BaseModel):
    job_id: UUID
    status: JobStatus
    ready: bool
    items: List[JobBacktestResultItem]
