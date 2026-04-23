#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

# 允许在仓库根执行测试时直接导入 src 下包
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from pipeline_job_api.app import create_app, get_engine
from pipeline_job_api.schemas import JobOut


def _job_out(*, status: str, run_mode: str) -> JobOut:
    return JobOut(
        job_id=uuid4(),
        status=status,
        source_type="manual",
        run_mode=run_mode,
        factor_ids=["F001"],
        test_universe="HS300",
        backtest_job_id=None,
        idempotency_key=None,
        error_message=None,
        result_summary=None,
        log_rel_path=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        finished_at=None,
        idempotent_replay=False,
    )


class TestPipelineJobApiA1SelectionOnly(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app()
        # 测试不依赖真实数据库连接
        self.app.dependency_overrides[get_engine] = lambda: object()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.app.dependency_overrides.clear()

    def test_selection_only_requires_backtest_job_id(self) -> None:
        payload = {
            "factor_ids": ["F001"],
            "source_type": "manual",
            "run_mode": "selection_only",
        }
        response = self.client.post("/api/v1/pipeline/jobs", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"],
            "BACKTEST_JOB_ID_REQUIRED",
        )

    def test_selection_only_rejects_not_found_source_job(self) -> None:
        payload = {
            "factor_ids": ["F001"],
            "source_type": "manual",
            "run_mode": "selection_only",
            "backtest_job_id": str(uuid4()),
        }
        with patch(
            "pipeline_job_api.app.job_store.get_job_by_public_id",
            return_value=None,
        ):
            response = self.client.post("/api/v1/pipeline/jobs", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"],
            "BACKTEST_JOB_NOT_FOUND",
        )

    def test_selection_only_rejects_non_success_source_job(self) -> None:
        payload = {
            "factor_ids": ["F001"],
            "source_type": "manual",
            "run_mode": "selection_only",
            "backtest_job_id": str(uuid4()),
        }
        with patch(
            "pipeline_job_api.app.job_store.get_job_by_public_id",
            return_value=_job_out(status="running", run_mode="new_only"),
        ):
            response = self.client.post("/api/v1/pipeline/jobs", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"],
            "BACKTEST_JOB_NOT_SUCCESS",
        )

    def test_selection_only_rejects_quick_source_job(self) -> None:
        payload = {
            "factor_ids": ["F001"],
            "source_type": "manual",
            "run_mode": "selection_only",
            "backtest_job_id": str(uuid4()),
        }
        with patch(
            "pipeline_job_api.app.job_store.get_job_by_public_id",
            return_value=_job_out(status="success", run_mode="quick"),
        ):
            response = self.client.post("/api/v1/pipeline/jobs", json=payload)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["error"]["code"],
            "BACKTEST_JOB_RUN_MODE_FORBIDDEN",
        )

    def test_selection_only_happy_path_passes_backtest_job_id(self) -> None:
        source_job_id = str(uuid4())
        created_job = _job_out(status="queued", run_mode="selection_only")
        payload = {
            "factor_ids": ["F001"],
            "source_type": "manual",
            "run_mode": "selection_only",
            "backtest_job_id": source_job_id,
        }
        with patch(
            "pipeline_job_api.app.job_store.get_job_by_public_id",
            return_value=_job_out(status="success", run_mode="new_only"),
        ), patch(
            "pipeline_job_api.app.job_store._fetch_missing_factor_ids",
            return_value=[],
        ), patch(
            "pipeline_job_api.app.job_store.create_job_queued",
            return_value=(created_job, False),
        ) as create_job_mock:
            response = self.client.post("/api/v1/pipeline/jobs", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["run_mode"],
            "selection_only",
        )
        self.assertEqual(
            create_job_mock.call_args.kwargs["backtest_job_id"],
            source_job_id,
        )


if __name__ == "__main__":
    unittest.main()
