#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from typing import List
from datetime import datetime

# 添加 common 模块到路径（对齐旧项目）
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import jqdatasdk
import pandas as pd
from sqlalchemy import text

from common.config import Config
from common.db import get_db_manager
from common.utils import setup_logger


logger = setup_logger("data_ingest_stock_daily_jq_initial", "logs/data_ingest_stock_daily_jq_initial.log")


def _parse_stock_codes(raw: str) -> List[str]:
    """解析配置中的股票代码列表，例如 '000001.SZ,600000.SH'"""
    if not raw:
        return []
    return [code.strip() for code in raw.split(",") if code.strip()]


def _normalize_jq_code_to_stock_code(jq_code: str) -> str:
    """
    聚宽代码 → 我们的 stock_code 规范（对齐 LQTP-core.DailyQuote.Symbol）

    - 股票：000001.XSHE / 600000.XSHG -> 000001.SZ / 600000.SH
    - 期货等：IF2403.CCFX / rb2405.XSGE 等去掉交易所后缀
    """
    if jq_code.endswith(".XSHE"):
        return jq_code.replace(".XSHE", ".SZ")
    if jq_code.endswith(".XSHG"):
        return jq_code.replace(".XSHG", ".SH")
    if jq_code.endswith(".CCFX"):
        return jq_code.replace(".CCFX", "")
    if jq_code.endswith(".XDCE"):
        return jq_code.replace(".XDCE", "")
    if jq_code.endswith(".XSGE"):
        return jq_code.replace(".XSGE", "")
    if jq_code.endswith(".XZCE"):
        return jq_code.replace(".XZCE", "")
    if jq_code.endswith(".XINE"):
        return jq_code.replace(".XINE", "")
    return jq_code  # 兜底，不做转换


def _resolve_universe(cfg: Config, end_date: str) -> List[str]:
    """
    根据配置解析股票池

    当前支持：
    - CUSTOM：使用 data_ingest_jq_initial.stock_codes（内部代码，如 000001.SZ）
    - STOCK：通过聚宽 get_all_securities(types=["stock"]) 拉全 A 股股票代码（返回聚宽原始代码，如 000001.XSHE）
    - ALL：对齐 JQsync，拉全市场 index/csi/stock/etf/lof/futures（返回聚宽原始代码，如 000001.XSHE / IF2403.CCFX）
    """
    universe = cfg.get("data_ingest_jq_initial", "universe", fallback="CUSTOM").upper()

    if universe == "CUSTOM":
        raw_codes = cfg.get("data_ingest_jq_initial", "stock_codes", fallback="")
        return _parse_stock_codes(raw_codes)

    if universe == "STOCK":
        logger.info(f"根据聚宽 get_all_securities 拉取全 A 股股票列表（STOCK），date={end_date}")
        df = jqdatasdk.get_all_securities(types=["stock"], date=end_date)

        # 返回聚宽原始代码（000001.XSHE / 600000.XSHG），后续在 main 中统一做规范化
        codes = list(df.index.to_list())
        logger.info(f"从聚宽获取全 A 股股票数量: {len(codes)}")
        return codes
    
    if universe == "INDEX":
        logger.info(f"根据聚宽 get_all_securities 拉取全市场指数列表（INDEX），date={end_date}")
        df = jqdatasdk.get_all_securities(types=["index"], date=end_date)
        codes = list(df.index.to_list())
        logger.info(f"从聚宽获取全市场指数数量: {len(codes)}")
        return codes
    
    if universe == "CSI":
        logger.info(f"根据聚宽 get_all_securities 拉取全市场CSI列表（CSI），date={end_date}")
        df = jqdatasdk.get_all_securities(types=["csi"], date=end_date)
        codes = list(df.index.to_list())
        logger.info(f"从聚宽获取全市场CSI数量: {len(codes)}")
        return codes
    
    if universe == "ETF":
        logger.info(f"根据聚宽 get_all_securities 拉取全市场ETF列表（ETF），date={end_date}")
        df = jqdatasdk.get_all_securities(types=["etf"], date=end_date)
        codes = list(df.index.to_list())
        logger.info(f"从聚宽获取全市场ETF数量: {len(codes)}")
        return codes
    
    if universe == "LOF":
        logger.info(f"根据聚宽 get_all_securities 拉取全市场LOF列表（LOF），date={end_date}")
        df = jqdatasdk.get_all_securities(types=["lof"], date=end_date)
        codes = list(df.index.to_list())
        logger.info(f"从聚宽获取全市场LOF数量: {len(codes)}")
        return codes
    
    if universe == "FUTURES":
        logger.info(f"根据聚宽 get_all_securities 拉取全市场期货列表（FUTURES），date={end_date}")
        df = jqdatasdk.get_all_securities(types=["futures"], date=end_date)
        codes = list(df.index.to_list())
        logger.info(f"从聚宽获取全市场期货数量: {len(codes)}")
        return codes
    
    if universe == "HS300":
        logger.info(f"拉取 HS300 成分股，date={end_date}")
        jq_codes = jqdatasdk.get_index_stocks("000300.XSHG", date=end_date)
        codes = jq_codes
        logger.info(f"HS300 成分数量: {len(codes)}")
        return codes


    if universe == "ALL":
        logger.info(f"根据聚宽 get_all_securities 拉取全市场合约列表，date={end_date}")
        df = jqdatasdk.get_all_securities(
            types=["index", "csi", "stock", "etf", "lof", "futures"],
            date=end_date,
        )
        codes = list(df.index.to_list())  # 保留聚宽原始代码，后续再标准化
        logger.info(f"从聚宽获取全市场合约数量: {len(codes)}")
        return codes

    raise ValueError(f"不支持的 universe 类型: {universe}")


def main():
    """从聚宽一次性拉历史 A 股日线数据，写入 stock_daily 表"""
    logger.info("启动 data_ingest_stock_daily_jq_initial 脚本")

    # 约定：本模块使用 src/data_ingest/config.ini（Config 内会自动切 *_dev.ini）
    config_file = "src/data_ingest/config.ini"
    cfg = Config(config_file=config_file)

    # 读取聚宽账号并登录（必须在调用任何 jqdatasdk 接口前完成）
    jq_user = cfg.get("jq", "user")
    jq_password = cfg.get("jq", "password")
    jqdatasdk.auth(jq_user, jq_password)
    logger.info("聚宽登录成功")

    # 读取导入时间区间
    start_date = cfg.get("data_ingest_jq_initial", "start_date")
    end_date = cfg.get("data_ingest_jq_initial", "end_date")
    logger.info(f"配置 - start_date={start_date}, end_date={end_date}")

    # 解析股票池（支持 CUSTOM / STOCK / INDEX / CSI / ETF / LOF / FUTURES / ALL）
    universe = cfg.get("data_ingest_jq_initial", "universe", fallback="CUSTOM").upper()
    stock_codes = _resolve_universe(cfg, end_date=end_date)
    if not stock_codes:
        logger.error("解析股票列表为空，请检查 data_ingest_jq_initial.universe / stock_codes 配置")
        return

    batch_size = cfg.getint("data_ingest_jq_initial", "batch_size", fallback=200)

    # 连接 Postgres（因子库）
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    inserted_total = 0

    try:
        # 聚宽字段：对齐 LQTP-core.dailyquote 的字段需求
        fields = [
            "open",
            "close",
            "low",
            "high",
            "volume",
            "money",
            "pre_close",
            "high_limit",
            "low_limit",
            "paused",
        ]

        # 如需期货合约乘数，对齐 JQsync 逻辑（仅在 ALL 下需要）
        futures_multiplier = {}
        if universe == "ALL" or universe == "FUTURES":
            future_list = jqdatasdk.get_all_securities(types=["futures"], date=end_date)
            if not future_list.empty:
                fut_codes = future_list.index.to_list()
                info = jqdatasdk.get_futures_info(securities=fut_codes, fields=("contract_multiplier",))
                futures_multiplier = {
                    k: int(v.get("contract_multiplier") or 1) for k, v in info.items()
                }
                logger.info(f"获取期货合约乘数数量: {len(futures_multiplier)}")

        # 按批次拉取，避免一次传入太多代码
        for i in range(0, len(stock_codes), batch_size):
            batch = stock_codes[i: i + batch_size]
            logger.info(f"开始拉取股票批次 {i} ~ {i + len(batch) - 1}，数量={len(batch)}")

            # 000001.SZ → 000001.XSHE, 600000.SH → 600000.XSHG
            jq_codes = []
            code_map = {}  # jq_code -> stock_code
            for code in batch:
                jq_codes.append(code)

                # code_map 始终映射到“内部标准代码”，对齐 LQTP-core.DailyQuote.Symbol
                internal_symbol = _normalize_jq_code_to_stock_code(code)
                code_map[code] = internal_symbol

            try:
                df = jqdatasdk.get_price(
                    jq_codes,
                    start_date=start_date,
                    end_date=end_date,
                    frequency="daily",
                    fq="none",
                    fields=fields,
                )
            except Exception as e:
                logger.error(f"从聚宽获取批次数据失败: {e}")
                continue

            if df is None or df.empty:
                logger.warning(f"聚宽返回空数据，批次 {i} ~ {i + len(batch) - 1}")
                continue

            # 对齐 JQsync.py：get_price 返回长表，列包含 time / code / open 等
            df = df.copy()
            df.reset_index(inplace=True)  # time -> 列

            records = []

            for _, row in df.iterrows():
                trade_dt = row["time"]
                trade_date = trade_dt.date()

                jq_code = row["code"]
                open_val = row["open"]
                high_val = row["high"]
                low_val = row["low"]
                close_val = row["close"]
                volume_val = row["volume"]
                money_val = row["money"]

                pre_close_val = row.get("pre_close")
                high_limit_val = row.get("high_limit")
                low_limit_val = row.get("low_limit")
                paused_val = row.get("paused")

                # 若主要字段全为 NaN，说明该日该标的无数据，跳过
                if pd.isna(close_val) and pd.isna(open_val):
                    continue

                stock_code = code_map.get(jq_code) or _normalize_jq_code_to_stock_code(jq_code)

                # 日收益：和 LQTP dailyquote 一致，单位为万分
                ret_val = None
                if pd.notna(close_val) and pd.notna(pre_close_val) and pre_close_val != 0:
                    ret_val = (close_val / pre_close_val - 1) * 10000

                # multiple：股票 = 1，期货按合约乘数，参考 JQsync 逻辑
                multiple_val = futures_multiplier.get(jq_code, 1)

                records.append(
                    {
                        "stock_code": stock_code,
                        "trade_date": trade_date,
                        "open": open_val,
                        "high": high_val,
                        "low": low_val,
                        "close": close_val,
                        "volume": volume_val,
                        "turnover": money_val,
                        "pre_close": pre_close_val,
                        "high_limit": high_limit_val,
                        "low_limit": low_limit_val,
                        "return": ret_val,
                        "is_suspend": bool(paused_val) if not pd.isna(paused_val) else None,
                        "multiple": multiple_val,
                        "update_time": datetime.now(),
                    }
                )

            if not records:
                logger.warning(f"批次 {i} ~ {i + len(batch) - 1} 在区间 {start_date} ~ {end_date} 无有效记录")
                continue

            logger.info(f"批次 {i} ~ {i + len(batch) - 1} 准备写入记录数: {len(records)}")

            insert_sql = text(
                """
                INSERT INTO stock_daily
                    (stock_code, trade_date,
                     open, high, low, close, volume, turnover,
                     pre_close, high_limit, low_limit, return,
                     is_suspend, multiple, update_time)
                VALUES
                    (:stock_code, :trade_date,
                     :open, :high, :low, :close, :volume, :turnover,
                     :pre_close, :high_limit, :low_limit, :return,
                     :is_suspend, :multiple, :update_time)
                ON CONFLICT (trade_date, stock_code) DO UPDATE SET
                    open        = EXCLUDED.open,
                    high        = EXCLUDED.high,
                    low         = EXCLUDED.low,
                    close       = EXCLUDED.close,
                    volume      = EXCLUDED.volume,
                    turnover    = EXCLUDED.turnover,
                    pre_close   = EXCLUDED.pre_close,
                    high_limit  = EXCLUDED.high_limit,
                    low_limit   = EXCLUDED.low_limit,
                    return      = EXCLUDED.return,
                    is_suspend  = EXCLUDED.is_suspend,
                    multiple    = EXCLUDED.multiple,
                    update_time = EXCLUDED.update_time
                """
            )

            try:
                session.execute(insert_sql, records)
                session.commit()
                inserted_total += len(records)
                logger.info(f"批次 {i} ~ {i + len(batch) - 1} 写入/更新 {len(records)} 条记录成功")
            except Exception as e:
                session.rollback()
                logger.error(f"批次 {i} ~ {i + len(batch) - 1} 写入失败，已回滚: {e}")

    finally:
        session.close()

    logger.info(f"data_ingest_stock_daily_jq_initial 结束，总写入/更新记录数: {inserted_total}")


if __name__ == "__main__":
    main()