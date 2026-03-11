#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from typing import List
from datetime import datetime, timedelta

# 添加 common 模块到路径（对齐旧项目）
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import akshare as ak
import pandas as pd
from sqlalchemy import text

from common.config import Config
from common.db import get_db_manager

from common.utils import setup_logger

logger = setup_logger("data_ingest_stock_daily", "logs/data_ingest_stock_daily.log")


def _parse_stock_codes(raw: str) -> List[str]:
    """解析配置中的股票代码列表"""
    if not raw:
        return []
    return [code.strip() for code in raw.split(",") if code.strip()]


def _resolve_universe(cfg: Config) -> List[str]:
    """根据配置解析股票池

    支持的 universe：CUSTOM / ALL_A / HS300 / ZZ500
    """
    universe = cfg.get("data_ingest", "universe", fallback="CUSTOM").upper()
    raw_custom = cfg.get("data_ingest", "stock_codes", fallback="").strip()

    if universe == "CUSTOM":
        return _parse_stock_codes(raw_custom)

    if universe == "ALL_A":
        df = ak.stock_zh_a_spot()
        codes: List[str] = []
        for _, row in df.iterrows():
            code = str(row.get("代码") or "").strip()
            if not code:
                continue
            # 约定：以 6 开头视为上交所，其余视为深交所
            if code.endswith(".SH") or code.endswith(".SZ"):
                codes.append(code)
            elif code.startswith("6"):
                codes.append(f"{code}.SH")
            else:
                codes.append(f"{code}.SZ")
        return codes

    if universe == "HS300":
        df = ak.index_stock_cons(symbol="000300")
    elif universe == "ZZ500":
        df = ak.index_stock_cons(symbol="000905")
    else:
        raise ValueError(f"未知的股票池类型 universe={universe}")

    codes: List[str] = []
    for _, row in df.iterrows():
        code = str(row.get("品种代码") or row.get("代码") or "").strip()
        if not code:
            continue
        # 指数成分通常没有后缀，这里默认按两市规则补
        if code.startswith("6"):
            codes.append(f"{code}.SH")
        else:
            codes.append(f"{code}.SZ")

    return codes


def main():
    """从 AkShare 拉取 A 股日线数据并写入 stock_daily 表（小区间测试版）"""
    logger.info("启动 data_ingest_stock_daily 脚本")

    # 约定：本模块使用 src/data_ingest/config.ini（Config 内会自动切到 *_dev.ini）
    config_file = "src/data_ingest/config.ini"
    cfg = Config(config_file=config_file)

    mode = cfg.get("data_ingest", "mode", fallback="full").lower()

    if mode == "full":
        start_date = cfg.get("data_ingest", "start_date", fallback="2024-01-01")
        end_date = cfg.get("data_ingest", "end_date", fallback=datetime.now().strftime("%Y-%m-%d"))
    elif mode == "daily":
        # 日更模式：回看 N 天（简单稳妥），避免遗漏
        lookback_days = cfg.getint("data_ingest", "daily_lookback_days", fallback=5)
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    else:
        logger.error(f"不支持的 data_ingest.mode = {mode}")
        return

    adjust = cfg.get("data_ingest", "adjust", fallback="qfq")

    stock_codes = _resolve_universe(cfg)
    if not stock_codes:
        logger.error("解析股票池后得到的股票列表为空，请检查 data_ingest.universe / stock_codes 配置")
        return

    logger.info(f"配置 - start_date={start_date}, end_date={end_date}, stock_codes={stock_codes}, adjust={adjust}")

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    inserted_total = 0

    try:
        for code in stock_codes:
            logger.info(f"开始拉取股票 {code} 的日线数据")
            try:
                # AkShare 要求日期格式为 YYYYMMDD
                start_str = start_date.replace("-", "")
                end_str = end_date.replace("-", "")

                # AkShare 的 symbol 一般不带 .SZ/.SH 后缀，这里仅用于请求，入库仍使用原始 code
                ak_symbol = code.split(".")[0]

                df = ak.stock_zh_a_hist(
                    symbol=ak_symbol,
                    period="daily",
                    start_date=start_str,
                    end_date=end_str,
                    adjust=adjust,
                )
            except Exception as e:
                logger.error(f"从 AkShare 获取 {code} 数据失败: {e}")
                continue

            if df is None or df.empty:
                logger.warning(f"AkShare 返回空数据，股票 {code}，区间 {start_date} ~ {end_date}")
                continue

            # 统一列名映射，确保后续处理稳定
            # 标准列：日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额
            expected_cols = ["日期", "开盘", "收盘", "最高", "最低", "成交量", "成交额"]
            missing = [col for col in expected_cols if col not in df.columns]
            if missing:
                logger.error(f"AkShare 返回列缺失，股票 {code} 缺少列: {missing}")
                continue

            df = df.copy()
            df["trade_date"] = pd.to_datetime(df["日期"]).dt.date

            records = []
            for _, row in df.iterrows():
                records.append(
                    {
                        "stock_code": code,
                        "trade_date": row["trade_date"],
                        "open": row["开盘"],
                        "high": row["最高"],
                        "low": row["最低"],
                        "close": row["收盘"],
                        "volume": row["成交量"],
                        "turnover": row["成交额"],
                    }
                )

            if not records:
                logger.warning(f"股票 {code} 在区间 {start_date} ~ {end_date} 无有效记录")
                continue

            logger.info(f"股票 {code} 准备写入记录数: {len(records)}")

            insert_sql = text(
                """
                INSERT INTO stock_daily
                    (stock_code, trade_date, open, high, low, close, volume, turnover)
                VALUES
                    (:stock_code, :trade_date, :open, :high, :low, :close, :volume, :turnover)
                ON CONFLICT (trade_date, stock_code) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    turnover = EXCLUDED.turnover
                """
            )

            try:
                session.execute(insert_sql, records)
                session.commit()
                inserted_total += len(records)
                logger.info(f"股票 {code} 写入/更新 {len(records)} 条记录成功")
            except Exception as e:
                session.rollback()
                logger.error(f"写入股票 {code} 数据失败，已回滚: {e}")

    finally:
        session.close()

    logger.info(f"data_ingest_stock_daily 结束，总写入/更新记录数: {inserted_total}")


if __name__ == "__main__":
    main()

