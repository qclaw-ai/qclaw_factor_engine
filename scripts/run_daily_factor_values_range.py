#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
批量按日期区间调用 daily_factor_values_runner。

默认区间：2026-04-14 ~ 2026-04-26（含两端）。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys


# 统一把 src 加入路径，复用项目内既有 runner 逻辑，避免复制实现。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.append(str(SRC_ROOT))

from common.config import Config  # noqa: E402
from common.utils import setup_logger  # noqa: E402
from daily_factor_values.daily_factor_values_runner import run_daily_factor_values  # noqa: E402


logger = setup_logger("run_daily_factor_values_range", "logs/run_daily_factor_values_range.log")


def _iter_dates(start_date: str, end_date: str):
    """按自然日迭代日期字符串（YYYY-MM-DD）。"""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if start > end:
        raise ValueError(f"start_date 不能晚于 end_date: {start_date} > {end_date}")

    current = start
    while current <= end:
        yield current.strftime("%Y-%m-%d")
        current = current + timedelta(days=1)


def main() -> None:
    parser = argparse.ArgumentParser(description="按日期区间批量跑 daily_factor_values_runner")
    parser.add_argument("--config", default="config.ini", help="根配置文件路径")
    parser.add_argument("--start-date", default="2026-04-14", help="起始日期（YYYY-MM-DD）")
    parser.add_argument("--end-date", default="2026-04-26", help="结束日期（YYYY-MM-DD）")
    parser.add_argument("--universe", default="", help="可选覆盖配置中的 [daily].universe")
    parser.add_argument("--scope", choices=("valid_only", "all_in_basic"), default="", help="可选覆盖配置中的 [daily].scope")
    parser.add_argument("--lookback-days", type=int, default=None, help="可选覆盖配置中的 [daily].lookback_days")
    parser.add_argument("--factor-ids", default="", help="可选：逗号分隔，仅联调少量因子")
    args = parser.parse_args()

    cfg = Config(config_file=args.config)

    # 若 CLI 没传覆盖值，则复用项目现有 daily 配置，和单日 runner 行为保持一致。
    lookback_days = args.lookback_days
    if lookback_days is None:
        lookback_days = cfg.getint("daily", "lookback_days", fallback=380)

    scope = (args.scope or cfg.get("daily", "scope", fallback="all_in_basic") or "all_in_basic").strip()
    if scope not in ("valid_only", "all_in_basic"):
        scope = "all_in_basic"

    universe = (args.universe or cfg.get("daily", "universe", fallback="ALL") or "ALL").strip()

    factor_ids_filter = [x.strip() for x in args.factor_ids.split(",") if x.strip()] if args.factor_ids.strip() else None

    logger.info(
        "批量日更启动 start=%s end=%s universe=%s scope=%s lookback_days=%s factor_ids=%s",
        args.start_date,
        args.end_date,
        universe,
        scope,
        lookback_days,
        factor_ids_filter or "ALL",
    )

    total = 0
    success = 0
    failed = 0

    for trade_date in _iter_dates(args.start_date, args.end_date):
        total += 1
        logger.info("开始执行 trade_date=%s", trade_date)
        try:
            # 复用 daily runner 主逻辑：内部会自动对齐到最近可用交易日。
            run_daily_factor_values(
                config_file=args.config,
                trade_date=trade_date,
                lookback_days=int(lookback_days),
                factor_ids_filter=factor_ids_filter,
                scope=scope,
                universe=universe,
            )
            success += 1
            logger.info("完成 trade_date=%s", trade_date)
        except Exception as e:
            failed += 1
            logger.exception("失败 trade_date=%s err=%s", trade_date, e)

    logger.info("批量日更结束 total=%s success=%s failed=%s", total, success, failed)

    # 非零失败时抛错，方便外层调度感知失败。
    if failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

