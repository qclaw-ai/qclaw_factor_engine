#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_daily 加载摘要日志：与 qclaw_strategy_engine 策略模板横幅格式对齐（72 列 '=' + 字段行），
前缀为 [stock_daily][因子工厂]，便于跨仓库 grep。
"""

from __future__ import annotations

import logging
from typing import Optional, Union


def log_stock_daily_banner(
    logger: logging.Logger,
    *,
    where: str,
    mode: str,
    start_date: str,
    end_date: str,
    n_stocks: Union[int, str],
    n_batches: Optional[int] = None,
    n_rows: Optional[int] = None,
) -> None:
    """醒目打印 stock_daily 加载摘要，便于在日志中快速检索。"""
    logger.info("=" * 72)
    logger.info(
        "[stock_daily][因子工厂] 场景=%s | 模式=%s | 区间=%s ~ %s | 股票数=%s | 批次数=%s | 行数=%s",
        where,
        mode,
        start_date,
        end_date,
        n_stocks,
        n_batches if n_batches is not None else "-",
        n_rows if n_rows is not None else "-",
    )
    logger.info("=" * 72)
