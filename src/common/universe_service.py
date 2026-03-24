#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Dict, List, Tuple

import jqdatasdk

from common.config import Config


def normalize_jq_code_to_stock_code(jq_code: str) -> str:
    """
    聚宽代码 -> 内部 stock_code 规范。

    - 股票：000001.XSHE / 600000.XSHG -> 000001.SZ / 600000.SH
    - 期货等：去掉交易所后缀（保持与现有 data_ingest 兼容）
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
    return jq_code


def normalize_stock_code_from_source_symbol(symbol: str) -> str:
    """
    源符号（可能是 jq_code 或内部代码）-> 内部 stock_code 规范。
    """
    if symbol is None:
        return ""
    s = str(symbol).strip()
    if not s:
        return ""
    return normalize_jq_code_to_stock_code(s)


def internal_stock_code_to_jq_code(internal_stock_code: str) -> str:
    """
    内部 stock_code（000001.SZ / 600000.SH）-> 聚宽 jq_code。
    """
    s = internal_stock_code.strip()
    if s.endswith(".SZ"):
        return s.replace(".SZ", ".XSHE")
    if s.endswith(".SH"):
        return s.replace(".SH", ".XSHG")
    return s


def normalize_universe_code(universe: str) -> str:
    """
    统一领域编码，兼容历史写法。
    约定：历史 ALL_A 映射到 ALL（你当前全市场 stock_daily 口径）。
    """
    u = (universe or "").strip().upper()
    if not u:
        return "CUSTOM"
    if u == "ALL_A":
        return "ALL"
    return u


def resolve_universe_for_jq(
    cfg: Config,
    end_date: str,
    section: str = "data_ingest",
) -> Tuple[List[str], List[str], Dict[str, str], str]:
    """
    解析领域并返回：
    - internal_stock_codes（用于 stock_daily 等内部表）
    - jq_codes（用于 jqdatasdk.get_price）
    - jq_code_to_internal 映射
    - 规范化后的 universe code

    兼容 jq_initial 的 _resolve_universe 语义，当前支持：
    - CUSTOM：配置的 stock_codes
    - STOCK：聚宽 stock
    - INDEX / CSI / ETF / LOF / FUTURES：聚宽对应 types
    - HS300 / ZZ500：指数成分股
    - ALL：聚宽 index/csi/stock/etf/lof/futures 合并
    """
    universe = normalize_universe_code(cfg.get(section, "universe", fallback="CUSTOM"))
    internal_stock_codes: List[str] = []
    jq_codes: List[str] = []
    jq_code_to_internal: Dict[str, str] = {}

    if universe == "CUSTOM":
        raw_codes = cfg.get(section, "stock_codes", fallback="").strip()
        if not raw_codes:
            return [], [], {}, universe
        for code in [x.strip() for x in raw_codes.split(",") if x.strip()]:
            internal = normalize_stock_code_from_source_symbol(code)
            jq_code = internal_stock_code_to_jq_code(internal)
            internal_stock_codes.append(internal)
            jq_codes.append(jq_code)
            jq_code_to_internal[jq_code] = internal
        return internal_stock_codes, jq_codes, jq_code_to_internal, universe

    if universe == "HS300":
        jq_codes = jqdatasdk.get_index_stocks("000300.XSHG", date=end_date)
    elif universe == "ZZ500":
        jq_codes = jqdatasdk.get_index_stocks("000905.XSHG", date=end_date)
    elif universe == "STOCK":
        df = jqdatasdk.get_all_securities(types=["stock"], date=end_date)
        jq_codes = list(df.index.to_list())
    elif universe == "INDEX":
        df = jqdatasdk.get_all_securities(types=["index"], date=end_date)
        jq_codes = list(df.index.to_list())
    elif universe == "CSI":
        df = jqdatasdk.get_all_securities(types=["csi"], date=end_date)
        jq_codes = list(df.index.to_list())
    elif universe == "ETF":
        df = jqdatasdk.get_all_securities(types=["etf"], date=end_date)
        jq_codes = list(df.index.to_list())
    elif universe == "LOF":
        df = jqdatasdk.get_all_securities(types=["lof"], date=end_date)
        jq_codes = list(df.index.to_list())
    elif universe == "FUTURES":
        df = jqdatasdk.get_all_securities(types=["futures"], date=end_date)
        jq_codes = list(df.index.to_list())
    elif universe == "ALL":
        df = jqdatasdk.get_all_securities(
            types=["index", "csi", "stock", "etf", "lof", "futures"],
            date=end_date,
        )
        jq_codes = list(df.index.to_list())
    else:
        raise ValueError(f"不支持的 universe 类型: {universe}")

    for jq_code in jq_codes:
        internal = normalize_jq_code_to_stock_code(jq_code)
        internal_stock_codes.append(internal)
        jq_code_to_internal[jq_code] = internal

    return internal_stock_codes, jq_codes, jq_code_to_internal, universe

