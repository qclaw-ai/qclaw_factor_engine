#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1 阶段 D：pipeline job worker（从仓库根运行，将 src 加入 Python 路径）。

示例:
  python run_pipeline_job_worker.py --config config_dev.ini --once
  python run_pipeline_job_worker.py --config config_dev.ini --loop --interval 30
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))


def main() -> None:
    # 与 factor_engine 等一致：终端 + 按日落盘 logs/pipeline_job_worker_YYYYMMDD.log
    from common.utils import setup_logger

    setup_logger(
        "pipeline_job_worker",
        os.path.join(ROOT, "logs", "pipeline_job_worker.log"),
    )

    p = argparse.ArgumentParser(description="factor_pipeline_job 单 worker")
    p.add_argument(
        "--config",
        default="config.ini",
        help="根 ini（相对路径相对仓库根），与 factor_engine / backtest_io 一致",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="只执行一轮（有任务则领一条并跑完，无则退出）",
    )
    p.add_argument(
        "--loop",
        action="store_true",
        help="无任务时按间隔休眠后继续轮询",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="--loop 时无任务时的休眠秒数，默认 30",
    )
    p.add_argument(
        "--running-timeout-minutes",
        type=int,
        default=30,
        help="running 超时回收阈值（分钟），默认 30",
    )
    args = p.parse_args()

    logging.getLogger("pipeline_job_worker").info(
        "启动参数 config=%s once=%s loop=%s",
        args.config,
        args.once,
        args.loop,
    )

    from pipeline_job_worker import worker

    if args.once and args.loop:
        p.error("--once 与 --loop 不能同时指定")

    if not args.once and not args.loop:
        args.once = True

    if args.loop:
        worker.run_loop(
            args.config,
            args.interval,
            running_timeout_minutes=args.running_timeout_minutes,
        )
    else:
        worker.process_next(
            args.config,
            running_timeout_minutes=args.running_timeout_minutes,
        )


if __name__ == "__main__":
    main()
