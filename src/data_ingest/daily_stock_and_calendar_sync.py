#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
daily_stock_and_calendar_sync.py

目标：每天同步两类数据到 db_factor：
1) stock_daily（满足 factor_engine_runner._load_stock_daily 的字段）
2) Calendar（供 strategy_output_builder 推导下一交易日 D）

支持“方案 1 + 方案 2”结合：
- 先尝试从外部 MySQL 源库同步（读取 DailyQuote / Calendar）
- 如果连不上 MySQL 或执行失败，则自动回退到聚宽 jqdatasdk

对齐假设（来自你现有的 JQsync.py）：
- MySQL 源库的表名默认：
  - DailyQuote：字段包括 TradeDate, Symbol, Open, High, Low, Close, Volume, Amount
  - Calendar：字段包括 TradeDate（YYYYMMDD 字符串）、IsTradeDay（1/0）
- db_factor 的目标表名（PostgreSQL，snake_case 风格）：
  - stock_daily：PRIMARY KEY (trade_date, stock_code)，字段 open/high/low/close/volume/turnover
  - calendar：PRIMARY KEY trade_date（DATE），字段 is_trade_day
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import jqdatasdk
from sqlalchemy import create_engine, text

# 对齐旧项目：把 src 加入路径，确保 common 能被导入
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.universe_service import (
    internal_stock_code_to_jq_code as _common_internal_stock_code_to_jq_code,
    normalize_jq_code_to_stock_code as _common_normalize_jq_code_to_stock_code,
    normalize_stock_code_from_source_symbol as _common_normalize_stock_code_from_source_symbol,
    resolve_universe_for_jq as _common_resolve_universe_for_jq,
)
from common.utils import setup_logger

logger = setup_logger("daily_stock_and_calendar_sync", "logs/daily_stock_and_calendar_sync.log")


def _normalize_jq_code_to_stock_code(jq_code: str) -> str:
    """
    聚宽代码 → 我们的 stock_code 规范（对齐 LQTP-core.DailyQuote.Symbol / factor_engine 的 stock_daily.stock_code）

    - 股票：000001.XSHE / 600000.XSHG -> 000001.SZ / 600000.SH
    - 期货等：IF2403.CCFX / rb2405.XSGE 等去掉交易所后缀
    """
    # 统一委托给 common.universe_service，避免多处实现分叉。
    return _common_normalize_jq_code_to_stock_code(jq_code)


def _normalize_stock_code_from_source_symbol(symbol: str) -> str:
    """
    MySQL DailyQuote.Symbol → stock_code 规范。

    这里做同样的归一化：如果源表已经写成 .SZ/.SH，则会原样返回。
    """
    # 统一委托给 common.universe_service，避免多处实现分叉。
    return _common_normalize_stock_code_from_source_symbol(symbol)


def _internal_stock_code_to_jq_code(internal_stock_code: str) -> str:
    """
    内部 stock_code（000001.SZ / 000001.SH）→ 聚宽 get_price 所需 jq_code。
    """
    # 统一委托给 common.universe_service，避免多处实现分叉。
    return _common_internal_stock_code_to_jq_code(internal_stock_code)


def _format_to_yyyymmdd(v) -> str:
    """
    把 TradeDate 统一成 YYYYMMDD 字符串。
    """
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return ""
        if "-" in s:
            return pd.to_datetime(s).strftime("%Y%m%d")
        # 假设已经是 YYYYMMDD
        if len(s) == 8 and s.isdigit():
            return s
        # 兜底尝试解析
        return pd.to_datetime(s).strftime("%Y%m%d")
    if isinstance(v, (datetime, pd.Timestamp)):
        return pd.to_datetime(v).strftime("%Y%m%d")
    if isinstance(v, date):
        return pd.to_datetime(v).strftime("%Y%m%d")

    # 兜底：尝试解析
    return pd.to_datetime(v).strftime("%Y%m%d")


def _parse_yyyymmdd_to_date(s: str) -> date:
    return pd.to_datetime(s, format="%Y%m%d").date()


def _ensure_calendar_table_exists(session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS calendar (
                trade_date    DATE PRIMARY KEY,
                is_trade_day  INTEGER NOT NULL,
                update_time   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )
    session.commit()


def _upsert_calendar_trade_days(
    session,
    trade_days_yyyymmdd: List[tuple[str, int]],
) -> int:
    """
    upsert calendar 的交易日/非交易日信息（写入 is_trade_day=0/1）。
    strategy_output_builder 推导下一交易日时仍会过滤 is_trade_day=1。
    """
    if not trade_days_yyyymmdd:
        return 0
    now = datetime.now()
    rows = []
    for d, is_trade_day in trade_days_yyyymmdd:
        td = pd.to_datetime(d, format="%Y%m%d").date()
        rows.append(
            {
                "trade_date": td,
                "is_trade_day": int(is_trade_day),
                "update_time": now,
            }
        )

    session.execute(
        text(
            """
            INSERT INTO calendar (trade_date, is_trade_day, update_time)
            VALUES (:trade_date, :is_trade_day, :update_time)
            ON CONFLICT (trade_date) DO UPDATE
            SET is_trade_day = EXCLUDED.is_trade_day,
                update_time = EXCLUDED.update_time
            """
        ),
        rows,
    )
    session.commit()
    return len(rows)


def _build_in_clause(param_prefix: str, items: List[str]) -> Tuple[str, Dict[str, object]]:
    """
    为 SQL: field IN (:p0, :p1, ...) 构造占位符与 params。
    """
    params: Dict[str, object] = {}
    placeholders: List[str] = []
    for i, v in enumerate(items):
        key = f"{param_prefix}{i}"
        placeholders.append(f":{key}")
        params[key] = v
    return "(" + ",".join(placeholders) + ")", params


def _resolve_universe_for_jq(cfg: Config, end_date: str) -> Tuple[List[str], List[str], Dict[str, str]]:
    """
    返回：
    - internal_stock_codes（用于 stock_daily）
    - jq_codes（用于 get_price）
    - jq_code_to_internal 映射
    """
    internal_stock_codes, jq_codes, jq_code_to_internal, _ = _common_resolve_universe_for_jq(
        cfg=cfg,
        end_date=end_date,
        section="data_ingest",
    )
    return internal_stock_codes, jq_codes, jq_code_to_internal


def _upsert_stock_daily(session, records: List[Dict[str, object]]) -> int:
    """
    批量 upsert stock_daily（trade_date, stock_code 为复合主键）。
    """
    if not records:
        return 0

    session.execute(
        text(
            """
            INSERT INTO stock_daily (
                stock_code, trade_date,
                open, high, low, close,
                volume, turnover,
                pre_close, high_limit, low_limit, "return",
                is_suspend, multiple, update_time
            ) VALUES (
                :stock_code, :trade_date,
                :open, :high, :low, :close,
                :volume, :turnover,
                :pre_close, :high_limit, :low_limit, :return,
                :is_suspend, :multiple, :update_time
            )
            ON CONFLICT (trade_date, stock_code) DO UPDATE SET
                open = EXCLUDED.open,
                high = EXCLUDED.high,
                low = EXCLUDED.low,
                close = EXCLUDED.close,
                volume = EXCLUDED.volume,
                turnover = EXCLUDED.turnover,
                pre_close = EXCLUDED.pre_close,
                high_limit = EXCLUDED.high_limit,
                low_limit = EXCLUDED.low_limit,
                "return" = EXCLUDED."return",
                is_suspend = EXCLUDED.is_suspend,
                multiple = EXCLUDED.multiple,
                update_time = EXCLUDED.update_time
            """
        ),
        records,
    )
    session.commit()
    return len(records)


def _sync_calendar_from_mysql(
    mysql_engine,
    target_session,
    cfg: Config,
    start_yyyymmdd: str,
    end_yyyymmdd: str,
) -> int:
    cal_table = cfg.get("mysql_source", "calendar_table", fallback="Calendar").strip()
    cal_fmt = cfg.get("mysql_source", "calendar_tradedate_format", fallback="yyyymmdd").strip().lower()
    calendar_sync_mode = cfg.get("mysql_source", "calendar_sync_mode", fallback="all").strip().lower()

    _ensure_calendar_table_exists(target_session)

    if calendar_sync_mode == "all":
        # Calendar 表通常很小；直接全量取（trade + non-trade），交给 upsert 消重
        sql = text(
            f"""
            SELECT TradeDate, IsTradeDay
            FROM {cal_table}
            """
        )
        df = pd.read_sql(sql, mysql_engine)
    else:
        # range：按目标区间取数，便于控制同步量
        start_param = start_yyyymmdd
        end_param = end_yyyymmdd
        if cal_fmt == "date":
            # 当源表 TradeDate 是 date/datetime 类型时，用 YYYY-MM-DD 供 BETWEEN 过滤
            start_param = pd.to_datetime(start_yyyymmdd, format="%Y%m%d").strftime("%Y-%m-%d")
            end_param = pd.to_datetime(end_yyyymmdd, format="%Y%m%d").strftime("%Y-%m-%d")

        sql = text(
            f"""
            SELECT TradeDate, IsTradeDay
            FROM {cal_table}
            WHERE TradeDate BETWEEN :start_yyyymmdd AND :end_yyyymmdd
            """
        )
        df = pd.read_sql(
            sql,
            mysql_engine,
            params={"start_yyyymmdd": start_param, "end_yyyymmdd": end_param},
        )

    if df.empty:
        return 0

    trade_days: List[tuple[str, int]] = []
    for _, r in df.iterrows():
        td = _format_to_yyyymmdd(r.get("TradeDate"))
        if td:
            is_trade_day = int(r.get("IsTradeDay") or 0)
            trade_days.append((td, is_trade_day))

    trade_days = sorted(set(trade_days))
    if not trade_days:
        return 0

    return _upsert_calendar_trade_days(target_session, trade_days)


def _sync_stock_daily_from_mysql(
    mysql_engine,
    target_session,
    cfg: Config,
    start_date: str,
    end_date: str,
    internal_stock_codes: List[str],
) -> int:
    dq_table = cfg.get("mysql_source", "dailyquote_table", fallback="DailyQuote").strip()
    date_fmt = cfg.get("mysql_source", "dailyquote_tradedate_format", fallback="date").strip().lower()
    stock_sync_chunk_days = cfg.getint("mysql_source", "stock_sync_chunk_days", fallback=3)

    _need_filter = bool(internal_stock_codes)
    symbol_clause = ""
    symbol_params: Dict[str, object] = {}
    if _need_filter:
        # 为避免 IN 太长，可自行在配置里调内部股票池（如 HS300）
        in_clause, params = _build_in_clause("sym_", internal_stock_codes)
        symbol_clause = f" AND Symbol IN {in_clause} "
        symbol_params = params

    sql = text(
        f"""
            SELECT
                TradeDate,
                Symbol,
                Open,
                High,
                Low,
                Close,
                PreClose,
                HighLimit,
                LowLimit,
                Volume,
                Amount,
                `Return`,
                IsSuspend,
                Multiple,
                UpdateTime
        FROM {dq_table}
        WHERE TradeDate BETWEEN :start_date AND :end_date
        {symbol_clause}
        """
    )

    written_total = 0
    cur = pd.to_datetime(start_date).date()
    final = pd.to_datetime(end_date).date()
    while cur <= final:
        # 注意：这里不要把 datetime.date 和 pandas.Timestamp 混着比较
        chunk_end = min(cur + timedelta(days=stock_sync_chunk_days - 1), final)
        chunk_start_str = cur.isoformat()
        chunk_end_str = chunk_end.isoformat()

        if date_fmt == "yyyymmdd":
            start = pd.to_datetime(chunk_start_str).strftime("%Y%m%d")
            end = pd.to_datetime(chunk_end_str).strftime("%Y%m%d")
        else:
            start = chunk_start_str
            end = chunk_end_str

        params = {"start_date": start, "end_date": end}
        params.update(symbol_params)

        df = pd.read_sql(sql, mysql_engine, params=params)
        if df.empty:
            cur = chunk_end + timedelta(days=1)
            continue

        records: List[Dict[str, object]] = []
        for _, r in df.iterrows():
            stock_code = _normalize_stock_code_from_source_symbol(str(r.get("Symbol") or "").strip())
            if not stock_code:
                continue
            trade_date_str = _format_to_yyyymmdd(r.get("TradeDate"))
            if not trade_date_str:
                continue
            trade_date = _parse_yyyymmdd_to_date(trade_date_str)

            # 目标表 turnover 对应源表 Amount（JQsync 里的 Amount=成交额）
            turnover = r.get("Amount")
            pre_close = r.get("PreClose")
            high_limit = r.get("HighLimit")
            low_limit = r.get("LowLimit")
            return_val = r.get("Return")
            paused_val = r.get("IsSuspend")
            multiple_val = r.get("Multiple")
            update_time = r.get("UpdateTime")


            records.append(
                {
                    "stock_code": stock_code,
                    "trade_date": trade_date,
                    "open": r.get("Open"),
                    "high": r.get("High"),
                    "low": r.get("Low"),
                    "close": r.get("Close"),
                    "volume": r.get("Volume"),
                    "turnover": turnover,
                    "pre_close": pre_close,
                    "high_limit": high_limit,
                    "low_limit": low_limit,
                    "return": return_val,
                    "is_suspend": bool(paused_val),
                    "multiple": multiple_val,
                    "update_time": update_time,
                }
            )

        written = _upsert_stock_daily(target_session, records)
        written_total += written

        cur = chunk_end + timedelta(days=1)

    return written_total


def _sync_calendar_from_jq(
    target_session,
    cfg: Config,
    start_date: date,
    end_date: date,
) -> int:
    user = cfg.get("jq", "user", fallback="").strip()
    password = cfg.get("jq", "password", fallback="").strip()
    if not user or not password:
        raise RuntimeError("config 缺少 [jq] user/password，无法调用 jqdatasdk")
    jqdatasdk.auth(user, password)

    all_trade_days = jqdatasdk.get_all_trade_days()
    trade_set = set()
    for td in all_trade_days:
        d = td.date() if hasattr(td, "date") else pd.to_datetime(td).date()
        trade_set.add(d)

    date_range = pd.date_range(start_date, end_date)
    trade_days: List[tuple[str, int]] = []
    for d in date_range:
        dd = pd.to_datetime(d).date()
        is_trade_day = 1 if dd in trade_set else 0
        trade_days.append((dd.strftime("%Y%m%d"), is_trade_day))

    if not trade_days:
        return 0

    return _upsert_calendar_trade_days(target_session, trade_days)


def _sync_stock_daily_from_jq(
    target_session,
    cfg: Config,
    start_date: str,
    end_date: str,
) -> int:
    user = cfg.get("jq", "user", fallback="").strip()
    password = cfg.get("jq", "password", fallback="").strip()
    if not user or not password:
        raise RuntimeError("config 缺少 [jq] user/password，无法调用 jqdatasdk")
    jqdatasdk.auth(user, password)

    end_date_for_universe = end_date
    internal_stock_codes, jq_codes, jq_code_to_internal = _resolve_universe_for_jq(cfg, end_date_for_universe)
    if not jq_codes:
        logger.warning("jq fallback：universe 无可用 jq_codes，跳过 stock_daily 同步")
        return 0

    batch_size = cfg.getint("sync", "jq_batch_size", fallback=200)
    # 对齐 JQsync.py / data_ingest_stock_daily_jq_initial.py 的字段口径
    fields = ["open", "high", "low", "close", "volume", "money", "pre_close", "high_limit", "low_limit", "paused"]

    ok_records: List[Dict[str, object]] = []
    total_written = 0

    for i in range(0, len(jq_codes), batch_size):
        batch = jq_codes[i : i + batch_size]
        logger.info("jq fallback：拉取股票批次 %d ~ %d，数量=%d", i, i + len(batch) - 1, len(batch))
        df = jqdatasdk.get_price(
            batch,
            start_date=start_date,
            end_date=end_date,
            frequency="daily",
            fq="none",
            fields=fields,
        )
        if df is None or df.empty:
            continue

        df = df.reset_index()
        # df: time / code / open/high/...
        for _, r in df.iterrows():
            trade_dt = r.get("time")
            if trade_dt is None or (isinstance(trade_dt, float) and pd.isna(trade_dt)):
                continue
            trade_date = pd.to_datetime(trade_dt).date()
            jq_code = str(r.get("code") or "").strip()
            stock_code = jq_code_to_internal.get(jq_code) or _normalize_jq_code_to_stock_code(jq_code)
            if not stock_code:
                continue

            open_val = r.get("open")
            close_val = r.get("close")
            high_val = r.get("high")
            low_val = r.get("low")
            volume_val = r.get("volume")
            money_val = r.get("money")
            pre_close_val = r.get("pre_close")
            high_limit_val = r.get("high_limit")
            low_limit_val = r.get("low_limit")
            paused_val = r.get("paused")

            # 若主要字段全为 NaN，跳过
            if pd.isna(close_val) and pd.isna(open_val):
                continue

            # 日收益：和 LQTP dailyquote 一致，单位为万分

            ret_val = None
            if pd.notna(close_val) and pre_close_val != 0:
                ret_val = (close_val / pre_close_val - 1) * 10000

            # 股票 multiple 默认 1
            multiple_val = 1
            is_suspend = bool(paused_val)

            ok_records.append(
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
                    "is_suspend": is_suspend,
                    "multiple": multiple_val,
                    "update_time": datetime.now(),
                }
            )

        # 分批 upsert，避免一次太大
        written = _upsert_stock_daily(target_session, ok_records)
        total_written += written
        ok_records = []

    return total_written


def _try_create_mysql_engine(cfg: Config):
    host = cfg.get("mysql_source", "host", fallback="").strip()
    user = cfg.get("mysql_source", "user", fallback="").strip()
    password = cfg.get("mysql_source", "password", fallback="").strip()
    db_name = cfg.get("mysql_source", "db_name", fallback="").strip()
    port = cfg.getint("mysql_source", "port", fallback=3306)

    if not host or not user or not db_name:
        return None

    # 需要 pymysql 支持；如果没有安装，会在 create_engine 时失败并触发 fallback
    from urllib.parse import quote_plus

    url = f"mysql+pymysql://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db_name}"
    return create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=3)


def run_sync(config_file: str, trade_date: str, lookback_days: int, calendar_buffer_days: int) -> None:
    cfg = Config(config_file=config_file)

    target_db_manager = get_db_manager(config_file=config_file)
    target_session = target_db_manager.get_session()

    try:
        t = pd.to_datetime(trade_date).date()
        start_d = (t - timedelta(days=int(lookback_days))).isoformat()
        end_d = t.isoformat()

        # Calendar 需要覆盖到下一个交易日（至少多给一个 buffer）
        cal_end = t + timedelta(days=int(calendar_buffer_days))
        start_yyyymmdd = pd.to_datetime(start_d).strftime("%Y%m%d")
        end_yyyymmdd = pd.to_datetime(cal_end.isoformat()).strftime("%Y%m%d")

        mysql_enabled = cfg.get("mysql_source", "enabled", fallback="false").strip().lower() in ("1", "true", "yes", "on")

        internal_stock_codes: List[str] = []
        # 为了 MySQL 查询过滤，尽量沿用配置的 universe（不要求全部股票）
        try:
            if mysql_enabled:
                symbol_filter_enabled = cfg.get("mysql_source", "symbol_filter", fallback="false").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
                # CUSTOM 可不依赖 jqdatasdk；其他 universe 若开启 symbol_filter，则用 jqdatasdk 解析出内部 stock_code 列表
                universe = cfg.get("data_ingest", "universe", fallback="CUSTOM").upper()
                if universe == "CUSTOM":
                    raw_codes = cfg.get("data_ingest", "stock_codes", fallback="").strip()
                    internal_stock_codes = [
                        _normalize_stock_code_from_source_symbol(x.strip())
                        for x in raw_codes.split(",")
                        if x.strip()
                    ]
                elif symbol_filter_enabled:
                    # 用于 MySQL Symbol IN 过滤，降低 stock_daily 写入量
                    _, jq_codes, jq_code_to_internal = _resolve_universe_for_jq(cfg, end_d)
                    internal_stock_codes = list(jq_code_to_internal.values())
        except Exception:
            internal_stock_codes = []

        # 先尝试 MySQL：calendar / stock 分开做，任一失败就对应回退
        mysql_engine = None
        if mysql_enabled:
            try:
                mysql_engine = _try_create_mysql_engine(cfg)
                if mysql_engine is not None:
                    with mysql_engine.connect() as conn:
                        conn.execute(text("SELECT 1"))
            except Exception as e:
                logger.warning("无法连接 MySQL，改用聚宽 fallback：%s", e)
                mysql_engine = None

        calendar_ok = False
        stock_ok = False

        if mysql_engine is not None:
            try:
                written_calendar = _sync_calendar_from_mysql(
                    mysql_engine=mysql_engine,
                    target_session=target_session,
                    cfg=cfg,
                    start_yyyymmdd=start_yyyymmdd,
                    end_yyyymmdd=end_yyyymmdd,
                )
                logger.info("MySQL 同步 Calendar 完成：写入 %d 行", written_calendar)
                calendar_ok = True
            except Exception as e:
                logger.error("MySQL 同步 Calendar 失败，将回退到聚宽：%s", e)

            try:
                # 如果 universe 非 CUSTOM，internal_stock_codes 可能为空；此时不做符号过滤
                written_stock = _sync_stock_daily_from_mysql(
                    mysql_engine=mysql_engine,
                    target_session=target_session,
                    cfg=cfg,
                    start_date=start_d,
                    end_date=end_d,
                    internal_stock_codes=internal_stock_codes,
                )
                logger.info("MySQL 同步 stock_daily 完成：写入 %d 行", written_stock)
                stock_ok = True
            except Exception as e:
                logger.error("MySQL 同步 stock_daily 失败，将回退到聚宽：%s", e)

        # 未成功的部分：用聚宽补齐
        if not calendar_ok:
            _ = _sync_calendar_from_jq(
                target_session=target_session,
                cfg=cfg,
                start_date=pd.to_datetime(start_d).date(),
                end_date=pd.to_datetime(cal_end.isoformat()).date(),
            )

        if not stock_ok:
            _ = _sync_stock_daily_from_jq(
                target_session=target_session,
                cfg=cfg,
                start_date=start_d,
                end_date=end_d,
            )

        logger.info(
            "daily_stock_and_calendar_sync 完成：trade_date=%s, lookback_days=%s, calendar_buffer_days=%s, mysql_engine_used=%s",
            trade_date,
            lookback_days,
            calendar_buffer_days,
            bool(mysql_engine),
        )
    finally:
        target_session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="同步 stock_daily + Calendar 到 db_factor（MySQL 可选，失败则回退聚宽）")
    parser.add_argument(
        "--config",
        default="src/data_ingest/config.ini",
        help="配置文件路径（需要 [database] + [jq]，以及可选 [mysql_source]）",
    )
    parser.add_argument(
        "--trade-date",
        default="",
        help="同步基准交易日 T（YYYY-MM-DD）。不传则默认用今天。stock_daily 同步区间为 [T-lookback_days, T]",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=5,
        help="确保 stock_daily 至少覆盖 T-lookback_days ~ T（因子滚动/REF 依赖）",
    )
    parser.add_argument(
        "--calendar-buffer-days",
        type=int,
        default=10,
        help="Calendar 额外同步到 T 后的多少天，避免找不到下一交易日 D",
    )

    args = parser.parse_args()
    trade_date_raw = args.trade_date.strip()
    if not trade_date_raw:
        trade_date_raw = date.today().isoformat()

    run_sync(
        config_file=args.config,
        trade_date=trade_date_raw,
        lookback_days=args.lookback_days,
        calendar_buffer_days=args.calendar_buffer_days,
    )


if __name__ == "__main__":
    main()

