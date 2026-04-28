#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
从 stock_daily 导出训练用 label：y_ret_1d、y_ret_5d。

约定：
- trade_date = t（特征日 / 因子观测日）
- y_ret_1d(t)：close 相对「下一交易日」收盘的收益
- y_ret_5d(t)：close 相对「往后第 5 个交易日」收盘的收益

二者均在 stock_daily 内按 (stock_code, trade_date) 排序后，用 shift(-k) 实现（k=1 与 k=5）。
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import jqdatasdk
import pandas as pd
import polars as pl
from sqlalchemy import bindparam, text

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.stock_daily_log import log_stock_daily_banner
from common.universe_service import normalize_universe_code, resolve_universe_for_jq
from common.utils import setup_logger

from .factor_export_runner import (
    _calc_as_of_trade_date,
    _load_existing_watermark,
    _month_bounds,
    _parse_iso_date,
    _write_json,
    _write_month_partitions,
)

logger = setup_logger("label_export_runner", "logs/label_export_runner.log")


def _auth_jq_if_configured(cfg: Config, config_file: str) -> None:
    """需要解析非 ALL/CUSTOM 域时登录聚宽（与 factor_engine 一致）。"""
    jq_user = ""
    jq_password = ""
    try:
        jq_user = (cfg.get("jq", "user", fallback="") or "").strip()
        jq_password = (cfg.get("jq", "password", fallback="") or "").strip()
    except Exception:
        jq_user = ""
        jq_password = ""

    if not jq_user or not jq_password:
        raise RuntimeError(
            f"未找到聚宽账号配置，无法解析分域股票池（config_file={config_file}）。"
        )

    jqdatasdk.auth(jq_user, jq_password)


def _load_stock_daily_close_range(
    config_file: str,
    start_date: str,
    end_date: str,
    universe: str,
    cfg: Config,
) -> pd.DataFrame:
    """
    从 stock_daily 拉取 close，列：stock_code, trade_date, close。

    分支口径与 factor_engine._load_stock_daily 对齐（含 SQL 侧按域过滤）。
    """
    u = normalize_universe_code(universe)
    use_sql_filter = u != "ALL"

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        base_select = """
        SELECT
            stock_code,
            trade_date,
            close
        FROM stock_daily
        WHERE trade_date BETWEEN :start_date AND :end_date
        """

        if not use_sql_filter:
            logger.info("label_export._load_stock_daily_close_range 分支=FULL_MARKET")
            params: dict = {"start_date": start_date, "end_date": end_date}
            df = pd.read_sql(text(base_select), session.bind, params=params)
            log_stock_daily_banner(
                logger,
                where="label_export._load_stock_daily_close_range",
                mode="FULL_MARKET",
                start_date=start_date,
                end_date=end_date,
                n_stocks="ALL",
                n_batches=1,
                n_rows=len(df),
            )

        elif u == "STOCK":
            sql = (
                base_select
                + " AND (stock_code LIKE '%.SH' OR stock_code LIKE '%.SZ')"
            )
            params = {"start_date": start_date, "end_date": end_date}
            df = pd.read_sql(text(sql), session.bind, params=params)
            log_stock_daily_banner(
                logger,
                where="label_export._load_stock_daily_close_range",
                mode="SQL_STOCK_SUFFIX",
                start_date=start_date,
                end_date=end_date,
                n_stocks="-",
                n_batches=1,
                n_rows=len(df),
            )

        elif u in ("CUSTOM", "HS300", "ZZ500", "INDEX", "CSI", "ETF", "LOF", "FUTURES"):
            _auth_jq_if_configured(cfg, config_file=config_file)
            internal_codes, _, _, _ = resolve_universe_for_jq(
                cfg=cfg,
                end_date=end_date,
                section="factor_engine",
                universe_hint=u,
            )
            if not internal_codes:
                logger.warning(
                    "resolve_universe_for_jq 返回空股票池（universe=%s），不加载 stock_daily",
                    u,
                )
                return pd.DataFrame()

            batch_size = 800
            frames: List[pd.DataFrame] = []
            sql_in = text(
                base_select + " AND stock_code IN :codes"
            ).bindparams(bindparam("codes", expanding=True))

            for i in range(0, len(internal_codes), batch_size):
                batch = internal_codes[i : i + batch_size]
                part = pd.read_sql(
                    sql_in,
                    session.bind,
                    params={
                        "start_date": start_date,
                        "end_date": end_date,
                        "codes": batch,
                    },
                )
                frames.append(part)

            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            log_stock_daily_banner(
                logger,
                where="label_export._load_stock_daily_close_range",
                mode=f"IN_universe_{u}",
                start_date=start_date,
                end_date=end_date,
                n_stocks=len(internal_codes),
                n_batches=max(1, (len(internal_codes) + batch_size - 1) // batch_size),
                n_rows=len(df),
            )

        else:
            raise ValueError(f"不支持的 universe（label_export）: {u}")

    finally:
        session.close()

    return df


def _compute_label_returns(
    pdf: pd.DataFrame,
    month_start: date,
    month_end: date,
) -> pl.DataFrame:
    """按股票内交易序列计算 y_ret_1d / y_ret_5d，并裁剪到目标自然月内的 trade_date。"""
    if pdf.empty:
        return pl.DataFrame(
            schema={
                "stock_code": pl.Utf8,
                "trade_date": pl.Utf8,
                "y_ret_1d": pl.Float64,
                "y_ret_5d": pl.Float64,
            }
        )

    pdf = pdf.copy()
    pdf["trade_date"] = pd.to_datetime(pdf["trade_date"]).dt.normalize()

    df = pl.from_pandas(pdf[["stock_code", "trade_date", "close"]])
    df = df.with_columns(pl.col("trade_date").cast(pl.Date))

    df = df.sort(["stock_code", "trade_date"])
    # 未来第 1、第 5 个交易行收盘价（不足则 null）
    df = df.with_columns(
        [
            pl.col("close").shift(-1).over("stock_code").alias("close_fwd_1"),
            pl.col("close").shift(-5).over("stock_code").alias("close_fwd_5"),
        ]
    )
    df = df.with_columns(
        [
            ((pl.col("close_fwd_1") / pl.col("close")) - 1.0).alias("y_ret_1d"),
            ((pl.col("close_fwd_5") / pl.col("close")) - 1.0).alias("y_ret_5d"),
        ]
    )

    df = df.filter(
        (pl.col("trade_date") >= pl.lit(month_start))
        & (pl.col("trade_date") <= pl.lit(month_end))
    )

    df = df.select(
        [
            pl.col("stock_code").cast(pl.Utf8),
            pl.col("trade_date").dt.strftime("%Y-%m-%d"),
            pl.col("y_ret_1d").cast(pl.Float64),
            pl.col("y_ret_5d").cast(pl.Float64),
        ]
    )
    df = df.sort(by=["trade_date", "stock_code"])
    return df


def run_label_export(
    config_file: str = "config.ini",
    *,
    universe: str,
    month: str,
    max_rows_per_part: int = 500_000,
    output_root_override: Optional[str] = None,
    sql_end_buffer_days: int = 45,
) -> None:
    """
    导出单月 label Parquet + manifest + watermark（按域、仅前进水位）。

    输出：
    - {output_root}/label/universe=U/month=YYYY-MM/part-*.parquet
    - {output_root}/meta/manifest/label/U/YYYY-MM.json
    - {output_root}/meta/watermark/label/U.json
    """
    u = normalize_universe_code(universe)
    month_start, month_end = _month_bounds(month)

    cfg = Config(config_file=config_file)
    output_root_cfg = cfg.get(
        "factor_export", "output_root", fallback="artifacts/factor_export_parquet"
    )
    output_root = Path(output_root_override or output_root_cfg).resolve()

    sql_start = month_start.isoformat()
    sql_end = (month_end + timedelta(days=max(1, int(sql_end_buffer_days)))).isoformat()

    logger.info(
        "label 导出启动 universe=%s month=%s sql_range=%s~%s output_root=%s",
        u,
        month,
        sql_start,
        sql_end,
        output_root,
    )

    pdf = _load_stock_daily_close_range(
        config_file=config_file,
        start_date=sql_start,
        end_date=sql_end,
        universe=u,
        cfg=cfg,
    )

    wide = _compute_label_returns(pdf, month_start=month_start, month_end=month_end)

    logger.info("label 宽表行数=%s（月份内交易日）", wide.height)

    part_rel_paths = _write_month_partitions(
        wide_df=wide,
        output_root=output_root,
        universe=u,
        month=month,
        max_rows_per_part=max_rows_per_part,
        dataset="label",
    )

    as_of_trade_date = _calc_as_of_trade_date(wide)

    manifest_path = (
        output_root / "meta" / "manifest" / "label" / u / f"{month}.json"
    )
    manifest: Dict[str, Any] = {
        "schema_version": "v1",
        "artifact_kind": "label",
        "label_columns": ["y_ret_1d", "y_ret_5d"],
        "label_definitions": {
            "y_ret_1d": (
                "close(按 stock_daily 排序后下一行) / close(t) - 1，trade_date=t"
            ),
            "y_ret_5d": (
                "close(按 stock_daily 排序后下第 5 行) / close(t) - 1，trade_date=t，"
                "为 5 个交易日持有期收益（非日历 5 日）"
            ),
        },
        "source_table": "stock_daily",
        "universe": u,
        "month": month,
        "sql_trade_date_range": [sql_start, sql_end],
        "as_of_trade_date": as_of_trade_date,
        "part_rel_paths": part_rel_paths,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_json(manifest_path, manifest)

    watermark_path = output_root / "meta" / "watermark" / "label" / f"{u}.json"
    watermark = {
        "schema_version": "v1",
        "artifact_kind": "label",
        "universe": u,
        "as_of_trade_date": as_of_trade_date,
        "month": month,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    existing_wm = _load_existing_watermark(watermark_path)
    old_as_of = _parse_iso_date(str(existing_wm.get("as_of_trade_date", "")))
    new_as_of = _parse_iso_date(as_of_trade_date)

    should_write_watermark = True
    if old_as_of is not None and new_as_of is not None and new_as_of < old_as_of:
        should_write_watermark = False
        logger.info(
            "跳过 label watermark 回退：existing_as_of=%s > new_as_of=%s",
            old_as_of,
            new_as_of,
        )

    if should_write_watermark:
        _write_json(watermark_path, watermark)
    else:
        logger.info("保留已有 label watermark=%s", watermark_path)

    logger.info(
        "label 导出完成 universe=%s month=%s parts=%s manifest=%s watermark=%s",
        u,
        month,
        len(part_rel_paths),
        manifest_path,
        watermark_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 stock_daily 导出 y_ret_1d / y_ret_5d（按月 Parquet）",
    )
    parser.add_argument(
        "--config",
        default="config.ini",
        help="根配置文件路径（dev 环境默认会自动切 _dev.ini）",
    )
    parser.add_argument(
        "--universe",
        required=True,
        help="领域代码，如 ZZ500/HS300/ALL",
    )
    parser.add_argument(
        "--month",
        required=True,
        help="目标月份 YYYY-MM",
    )
    parser.add_argument(
        "--max-rows-per-part",
        type=int,
        default=500_000,
        help="每个 parquet part 的最大行数",
    )
    parser.add_argument(
        "--sql-end-buffer-days",
        type=int,
        default=45,
        help=(
            "SQL 查询结束日期相对月末的缓冲日历天数；"
            "需覆盖当月最后若干交易日后所需的未来 close（尤其 y_ret_5d）"
        ),
    )
    parser.add_argument(
        "--output-root",
        default="",
        help="输出根目录，默认读 [factor_export].output_root",
    )
    args = parser.parse_args()

    run_label_export(
        config_file=args.config,
        universe=args.universe,
        month=args.month,
        max_rows_per_part=int(args.max_rows_per_part),
        sql_end_buffer_days=int(args.sql_end_buffer_days),
        output_root_override=args.output_root.strip() or None,
    )


if __name__ == "__main__":
    main()
