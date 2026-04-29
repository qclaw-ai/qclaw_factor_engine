#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1 阶段 C：从仓库根启动 pipeline job API（将 src 加入 Python 路径）。

用法（示例）:
  set PIPELINE_JOB_API_CONFIG=config_dev.ini
  python run_pipeline_job_api.py --config config_dev.ini --port 8777
"""

from __future__ import annotations

import argparse
import os
import sys

# 当前文件位于 src/pipeline_job_api/ 下，这里把仓库根加入 sys.path，使其行为与旧版根目录脚本一致。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> None:
    import uvicorn

    p = argparse.ArgumentParser(description="启动 factor_pipeline_job HTTP API")
    p.add_argument(
        "--config",
        default="config.ini",
        help="根 ini 路径（相对则相对仓库根），与 common.Config 的 dev 切换一致",
    )
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8777)
    args = p.parse_args()
    cfg = os.path.abspath(
        args.config
        if os.path.isabs(args.config)
        else os.path.join(ROOT, args.config)
    )
    os.environ["PIPELINE_JOB_API_CONFIG"] = cfg
    uvicorn.run(
        "pipeline_job_api.app:app",
        host=args.host,
        port=args.port,
        factory=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
