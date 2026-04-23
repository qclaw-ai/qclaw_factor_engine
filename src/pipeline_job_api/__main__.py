# -*- coding: utf-8 -*-
"""
在 src 为当前工作目录时: python -m pipeline_job_api
（需能 import common；通常与 run_pipeline_job_api.py 二选一即可）
"""

from __future__ import annotations

import argparse
import os
import sys

_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def main() -> None:
    import uvicorn
    from common.config import PROJECT_ROOT

    p = argparse.ArgumentParser()
    p.add_argument("--config", default="config.ini")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8777)
    args = p.parse_args()
    cfg = args.config
    if not os.path.isabs(cfg):
        cfg = os.path.join(PROJECT_ROOT, cfg)
    os.environ["PIPELINE_JOB_API_CONFIG"] = os.path.abspath(cfg)
    uvicorn.run("pipeline_job_api.app:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
