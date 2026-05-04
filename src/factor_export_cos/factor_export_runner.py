#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import polars as pl
from sqlalchemy import text

# 对齐仓库其他 runner：将 src 目录加入路径，便于导入 common.*
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.universe_service import normalize_universe_code
from common.utils import setup_logger
from factor_engine.factor_engine_runner import _daily_parquet_bundle_path

logger = setup_logger("factor_export_runner", "logs/factor_export_runner.log")


@dataclass
class BatchSourceRow:
    factor_id: str
    rel_path: str
    date_start: str
    date_end: str
    is_rebase: bool
    created_at: str
    row_id: int
    # yearly_parquet 行：对应自然年；其它用途保留 Optional
    year: Optional[int] = None


@dataclass
class FactorBatchGroup:
    """按 factor_id 分组后的批次输入（yearly_parquet；日更 patch 读磁盘 daily bundle）。"""
    factor_ids: List[str]
    yearly_rows: List[BatchSourceRow]


def _project_root() -> Path:
    """仓库根目录。"""
    return Path(__file__).resolve().parents[2]


def _month_bounds(month: str) -> tuple[date, date]:
    """将 YYYY-MM 转换为当月起止日期。"""
    m = month.strip()
    if len(m) != 7 or m[4] != "-":
        raise ValueError(f"month 格式非法，期望 YYYY-MM，实际={month}")

    start = datetime.strptime(m + "-01", "%Y-%m-%d").date()
    if start.month == 12:
        next_month = date(start.year + 1, 1, 1)
    else:
        next_month = date(start.year, start.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start, end


def _resolve_abs_path(project_root: Path, rel_path: str) -> Path:
    """仓库相对路径转绝对路径。"""
    norm = (rel_path or "").strip().replace("/", os.sep)
    if not norm:
        raise ValueError("空 rel_path 不可用")
    return (project_root / norm).resolve()


def _safe_parse_date(value: Any) -> Optional[date]:
    """兼容 datetime/date/字符串的日期解析。"""
    if value is None:
        return None

    if isinstance(value, date):
        return value

    s = str(value).strip()
    if not s:
        return None

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _discover_daily_parquet_bundle_dates(
    project_root: Path,
    universe: str,
    month_start: date,
    month_end: date,
    recent_days: int,
) -> List[date]:
    """
    列出目标月内、且落在 ``recent_days`` 回看窗口内的日更 ``factors.parquet`` 交易日（升序）。

    路径约定与 ``factor_engine_runner._daily_parquet_bundle_path`` 一致。
    """
    if recent_days > 0:
        window_start = month_end - timedelta(days=recent_days - 1)
        effective_start = max(month_start, window_start)
    else:
        effective_start = month_start

    base = project_root / "factor_values_parquet" / "daily" / "by_universe" / universe

    if not base.is_dir():
        logger.warning("未发现 daily universe 目录 path=%s", base)
        return []

    out: List[date] = []

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue

        try:
            td = date.fromisoformat(child.name[:10])
        except ValueError:
            continue

        if td < effective_start or td > month_end:
            continue

        pq = child / "factors.parquet"

        if pq.is_file():
            out.append(td)

    return out


def _collect_factor_ids_from_daily_bundles(
    project_root: Path,
    universe: str,
    trade_dates: List[date],
) -> Set[str]:
    """从各日 ``factors.parquet`` 收集出现过的 ``factor_id``（轻量 scan）。"""
    out: Set[str] = set()

    for td in trade_dates:
        p = _daily_parquet_bundle_path(project_root, universe, td.isoformat())

        if not p.is_file():
            continue

        try:
            s = pl.scan_parquet(str(p)).select(pl.col("factor_id").unique())
            ids = s.collect()["factor_id"].to_list()
        except Exception as e:
            logger.warning("扫描 daily bundle factor_id 失败 path=%s err=%s", p, e)
            continue

        for x in ids:
            if x is None:
                continue
            t = str(x).strip()

            if t:
                out.add(t)

    return out


def _read_daily_parquet_bundle_for_export(
    bundle_path: Path,
    universe: str,
    month_start: date,
    month_end: date,
    factor_id_allow: Optional[Set[str]],
    trade_date: date,
) -> Optional[pl.DataFrame]:
    """
    读取单日多因子 ``factors.parquet``，标准化为与 ``_read_factor_parquet`` 相同的长表 schema。

    ``source_priority=2``：与历史 daily_csv patch 同级，覆盖 yearly（``unique(..., keep='last')``）。
    """
    if not bundle_path.is_file():
        return None

    try:
        df = pl.read_parquet(str(bundle_path))
    except Exception as e:
        logger.error("读取 daily bundle 失败 path=%s err=%s", bundle_path, e)
        return None

    if df.height == 0:
        return None

    cols = set(df.columns)

    if "trade_date" not in cols and "date" in cols:
        df = df.rename({"date": "trade_date"})

    required = {"stock_code", "trade_date", "factor_id", "factor_value"}
    if not required.issubset(cols):
        logger.warning(
            "daily bundle 缺少必需列，跳过 path=%s need=%s got=%s",
            bundle_path,
            sorted(required),
            df.columns,
        )
        return None

    if factor_id_allow is not None:
        df = df.filter(pl.col("factor_id").is_in(sorted(factor_id_allow)))

    month_start_s = month_start.strftime("%Y-%m-%d")
    month_end_s = month_end.strftime("%Y-%m-%d")
    row_id = int(trade_date.strftime("%Y%m%d"))
    created_at = ""

    df = (
        df.with_columns(
            [
                pl.col("trade_date").cast(pl.Utf8).str.slice(0, 10).alias("trade_date"),
                pl.col("stock_code").cast(pl.Utf8),
                pl.col("factor_id").cast(pl.Utf8),
                pl.col("factor_value").cast(pl.Float64),
            ]
        )
        .filter(
            (pl.col("trade_date") >= month_start_s) &
            (pl.col("trade_date") <= month_end_s)
        )
        .with_columns(
            [
                pl.lit(universe).alias("universe"),
                pl.lit("daily_parquet").alias("source"),
                pl.lit(2).alias("source_priority"),
                pl.lit(0).alias("is_rebase"),
                pl.lit(created_at).alias("created_at"),
                pl.lit(int(row_id)).alias("row_id"),
            ]
        )
        .select(
            [
                "universe",
                "factor_id",
                "stock_code",
                "trade_date",
                "factor_value",
                "source",
                "source_priority",
                "is_rebase",
                "created_at",
                "row_id",
            ]
        )
    )

    if df.height == 0:
        return None

    return df


def _fetch_yearly_parquet_sources(
    config_file: str,
    universe: str,
    stage: str,
    month_start: date,
    month_end: date,
) -> List[BatchSourceRow]:
    """
    从 factor_value_files 查询与目标月份有交集的 yearly_parquet 行（按 factor_id+year 取最新一条）。
    """
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()
    try:
        rows = session.execute(
            text(
                """
                SELECT DISTINCT ON (factor_id, year)
                    factor_id,
                    rel_path,
                    date_start,
                    date_end,
                    year,
                    COALESCE(is_rebase, FALSE) AS is_rebase,
                    COALESCE(created_at, CURRENT_TIMESTAMP) AS created_at,
                    id
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'yearly_parquet'
                  AND stage = :stage
                  AND rel_path IS NOT NULL
                  AND rel_path <> ''
                  AND date_start IS NOT NULL
                  AND date_end IS NOT NULL
                  AND date_start <= :month_end
                  AND date_end >= :month_start
                ORDER BY factor_id, year, is_rebase DESC, created_at DESC, id DESC
                """
            ),
            {
                "universe": universe,
                "stage": stage,
                "month_start": month_start,
                "month_end": month_end,
            },
        ).mappings().all()
    finally:
        session.close()

    out: List[BatchSourceRow] = []
    for r in rows:
        yr = r.get("year")
        y_int: Optional[int] = int(yr) if yr is not None else None
        out.append(
            BatchSourceRow(
                factor_id=str(r["factor_id"]).strip(),
                rel_path=str(r["rel_path"]).strip(),
                date_start=str(r["date_start"]),
                date_end=str(r["date_end"]),
                is_rebase=bool(r["is_rebase"]),
                created_at=str(r["created_at"]),
                row_id=int(r["id"]),
                year=y_int,
            )
        )
    return out


def _read_factor_parquet(
    abs_path: Path,
    factor_id: str,
    universe: str,
    month_start: date,
    month_end: date,
    source: str,
    source_priority: int,
    is_rebase: int,
    created_at: str,
    row_id: int,
) -> Optional[pl.DataFrame]:
    """
    读取 yearly 单因子长表 Parquet（列含 ``trade_date, stock_code, factor_value``），标准化为导出用长表 schema。
    """
    if not abs_path.exists():
        logger.warning("Parquet 不存在，跳过: %s", abs_path)
        return None

    try:
        df = pl.read_parquet(str(abs_path))
    except Exception as e:
        logger.error("读取 Parquet 失败 path=%s err=%s", abs_path, e)
        return None

    if df.height == 0:
        return None

    cols = set(df.columns)
    if "trade_date" not in cols and "date" in cols:
        df = df.rename({"date": "trade_date"})

    required = {"stock_code", "trade_date", "factor_value"}
    if not required.issubset(cols):
        logger.warning(
            "Parquet 缺少必需列，跳过 path=%s need=%s got=%s",
            abs_path,
            sorted(required),
            df.columns,
        )
        return None

    month_start_s = month_start.strftime("%Y-%m-%d")
    month_end_s = month_end.strftime("%Y-%m-%d")

    df = (
        df.with_columns(
            [
                pl.col("trade_date").cast(pl.Utf8).str.slice(0, 10).alias("trade_date"),
                pl.col("stock_code").cast(pl.Utf8),
                pl.col("factor_value").cast(pl.Float64),
            ]
        )
        .filter(
            (pl.col("trade_date") >= month_start_s) &
            (pl.col("trade_date") <= month_end_s)
        )
        .with_columns(
            [
                pl.lit(factor_id).alias("factor_id"),
                pl.lit(universe).alias("universe"),
                pl.lit(source).alias("source"),
                pl.lit(int(source_priority)).alias("source_priority"),
                pl.lit(int(is_rebase)).alias("is_rebase"),
                pl.lit(created_at).alias("created_at"),
                pl.lit(int(row_id)).alias("row_id"),
            ]
        )
        .select(
            [
                "universe",
                "factor_id",
                "stock_code",
                "trade_date",
                "factor_value",
                "source",
                "source_priority",
                "is_rebase",
                "created_at",
                "row_id",
            ]
        )
    )

    if df.height == 0:
        return None

    return df


def _merge_and_dedupe(long_frames: List[pl.DataFrame]) -> pl.DataFrame:
    """
    合并并按规则去重。

    冲突优先级（由低到高，``source_priority`` 递增，``unique(..., keep='last')`` 保留大者）：
    1) yearly_parquet（1）
    2) daily_parquet bundle（2，同键覆盖 yearly）
    """
    if not long_frames:
        return pl.DataFrame(
            schema={
                "universe": pl.Utf8,
                "factor_id": pl.Utf8,
                "stock_code": pl.Utf8,
                "trade_date": pl.Utf8,
                "factor_value": pl.Float64,
            }
        )

    df = pl.concat(long_frames, how="vertical")

    # 排序后对主键 unique keep=last，保证“后者优先”。
    df = df.sort(
        by=["universe", "factor_id", "stock_code", "trade_date", "source_priority", "is_rebase", "created_at", "row_id"],
        descending=[False, False, False, False, False, False, False, False],
    )
    df = df.unique(
        subset=["universe", "factor_id", "stock_code", "trade_date"],
        keep="last",
    )
    return df


def _build_wide(long_df: pl.DataFrame) -> pl.DataFrame:
    """长表转宽表，输出可直接训练。"""
    if long_df.height == 0:
        return pl.DataFrame(schema={"stock_code": pl.Utf8, "trade_date": pl.Utf8})

    wide = long_df.pivot(
        index=["stock_code", "trade_date"],
        on="factor_id",
        values="factor_value",
        aggregate_function="first",
    )

    wide = wide.sort(by=["trade_date", "stock_code"])
    return wide


def _write_month_partitions(
    wide_df: pl.DataFrame,
    output_root: Path,
    universe: str,
    month: str,
    max_rows_per_part: int,
    *,
    dataset: str = "factor",
) -> List[str]:
    """写月分区 parquet，返回相对路径列表。

    :param dataset: 顶层目录名，如 factor / label。
    """
    out_dir = output_root / dataset / f"universe={universe}" / f"month={month}"
    os.makedirs(out_dir, exist_ok=True)

    # 每次重建先清理旧 part，保证幂等。
    for p in out_dir.glob("part-*.parquet"):
        p.unlink(missing_ok=True)

    if wide_df.height == 0:
        return []

    rel_paths: List[str] = []
    rows = wide_df.height
    step = max(1, int(max_rows_per_part))
    idx = 0
    start = 0
    while start < rows:
        chunk = wide_df.slice(offset=start, length=step)
        name = f"part-{idx:03d}.parquet"
        abs_path = out_dir / name
        chunk.write_parquet(str(abs_path), compression="zstd")
        rel_paths.append(abs_path.relative_to(output_root).as_posix())
        start += step
        idx += 1

    return rel_paths


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    """写 JSON（UTF-8，缩进）。"""
    os.makedirs(path.parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _calc_as_of_trade_date(wide_df: pl.DataFrame) -> str:
    """从宽表计算 as_of_trade_date。"""
    if wide_df.height == 0 or "trade_date" not in wide_df.columns:
        return ""
    value = wide_df.select(pl.col("trade_date").max()).to_series().to_list()
    if not value:
        return ""
    return str(value[0])


def _parse_iso_date(date_str: str) -> Optional[date]:
    """解析 YYYY-MM-DD；非法返回 None。"""
    s = (date_str or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _load_existing_watermark(path: Path, legacy_path: Optional[Path] = None) -> Dict[str, Any]:
    """读取已有 watermark，不存在或损坏返回空字典（兼容旧路径回退读取）。"""
    candidates: List[Path] = [path]
    if legacy_path is not None:
        candidates.append(legacy_path)

    for p in candidates:
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            continue
    return {}


def _group_sources_by_factor(
    yearly_rows: List[BatchSourceRow],
    all_factor_ids: List[str],
    factor_batch_size: int,
) -> List[FactorBatchGroup]:
    """
    按 factor_id 切批，降低内存峰值。

    规则：
    - ``all_factor_ids`` 为 yearly 与日更 bundle 中出现因子的并集（调用方已排序）
    - 每批最多 ``factor_batch_size`` 个因子
    """
    if factor_batch_size <= 0:
        raise ValueError(f"factor_batch_size 必须 > 0，实际={factor_batch_size}")

    if not all_factor_ids:
        return []

    yearly_map: Dict[str, List[BatchSourceRow]] = {}
    for r in yearly_rows:
        yearly_map.setdefault(r.factor_id, []).append(r)

    groups: List[FactorBatchGroup] = []
    start = 0

    while start < len(all_factor_ids):
        chunk = all_factor_ids[start:start + factor_batch_size]
        y_rows: List[BatchSourceRow] = []

        for fid in chunk:
            y_rows.extend(yearly_map.get(fid, []))

        groups.append(
            FactorBatchGroup(
                factor_ids=chunk,
                yearly_rows=y_rows,
            )
        )
        start += factor_batch_size

    return groups


def run_export(
    config_file: str = "config.ini",
    *,
    universe: str,
    month: str,
    stage: str = "candidate",
    include_daily: bool = True,
    daily_recent_days: int = 3,
    factor_batch_size: int = 50,
    max_rows_per_part: int = 300_000,
    output_root_override: Optional[str] = None,
) -> None:
    """
    执行月分区 Parquet 导出（candidate 阶段）。

    - 主读：``factor_value_files.yearly_parquet``（与目标月有区间交集，``DISTINCT ON (factor_id, year)`` 取最新行）
    - 日更 patch：磁盘 ``factor_values_parquet/daily/by_universe/{U}/{trade_date}/factors.parquet``（可选）
    - 冲突规则：同键 ``daily_parquet`` > ``yearly``（``source_priority`` + ``unique(..., keep='last')``）
    """
    u = normalize_universe_code(universe)
    month_start, month_end = _month_bounds(month)

    cfg = Config(config_file=config_file)
    output_root_cfg = cfg.get("factor_export", "output_root", fallback="artifacts/factor_export_parquet")
    output_root = Path(output_root_override or output_root_cfg).resolve()

    logger.info(
        "导出启动 universe=%s month=%s stage=%s include_daily=%s daily_recent_days=%s "
        "factor_batch_size=%s output_root=%s",
        u,
        month,
        stage,
        include_daily,
        daily_recent_days,
        factor_batch_size,
        output_root,
    )

    project_root = _project_root()
    yearly_rows = _fetch_yearly_parquet_sources(
        config_file=config_file,
        universe=u,
        stage=stage,
        month_start=month_start,
        month_end=month_end,
    )
    logger.info("命中 yearly_parquet 源文件记录数=%s", len(yearly_rows))

    daily_bundle_dates: List[date] = []

    if include_daily:
        daily_bundle_dates = _discover_daily_parquet_bundle_dates(
            project_root=project_root,
            universe=u,
            month_start=month_start,
            month_end=month_end,
            recent_days=daily_recent_days,
        )

    logger.info("命中 daily_parquet bundle 交易日数量=%s", len(daily_bundle_dates))

    yearly_factor_ids = {r.factor_id for r in yearly_rows}
    daily_factor_ids: Set[str] = set()

    if daily_bundle_dates:
        daily_factor_ids = _collect_factor_ids_from_daily_bundles(
            project_root,
            u,
            daily_bundle_dates,
        )

    all_factor_ids = sorted(yearly_factor_ids | daily_factor_ids)

    source_groups = _group_sources_by_factor(
        yearly_rows=yearly_rows,
        all_factor_ids=all_factor_ids,
        factor_batch_size=int(factor_batch_size),
    )
    logger.info("因子分批完成 batch_count=%s", len(source_groups))

    wide_acc: Optional[pl.DataFrame] = None
    used_yearly_files: List[str] = []
    used_batch_files: List[str] = []
    used_daily_files: List[str] = []
    all_daily_trade_dates: List[str] = []

    for td in daily_bundle_dates:
        bp = _daily_parquet_bundle_path(project_root, u, td.isoformat())

        if not bp.is_file():
            continue

        try:
            rel_dp = bp.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            rel_dp = bp.resolve().as_posix()

        used_daily_files.append(rel_dp)

    used_daily_files = sorted(set(used_daily_files))

    for idx, grp in enumerate(source_groups, start=1):
        logger.info(
            "处理因子批次 %s/%s factor_count=%s",
            idx,
            len(source_groups),
            len(grp.factor_ids),
        )

        long_frames: List[pl.DataFrame] = []
        fid_allow = set(grp.factor_ids)

        # yearly_parquet：source_priority=1
        for r in grp.yearly_rows:
            abs_path = _resolve_abs_path(project_root, r.rel_path)
            df = _read_factor_parquet(
                abs_path=abs_path,
                factor_id=r.factor_id,
                universe=u,
                month_start=month_start,
                month_end=month_end,
                source="yearly",
                source_priority=1,
                is_rebase=1 if r.is_rebase else 0,
                created_at=r.created_at,
                row_id=r.row_id,
            )
            if df is not None and df.height > 0:
                long_frames.append(df)
                used_yearly_files.append(r.rel_path)

        # daily_parquet bundle：source_priority=2（同键覆盖 yearly）
        for td in daily_bundle_dates:
            bp = _daily_parquet_bundle_path(project_root, u, td.isoformat())
            df = _read_daily_parquet_bundle_for_export(
                bundle_path=bp,
                universe=u,
                month_start=month_start,
                month_end=month_end,
                factor_id_allow=fid_allow,
                trade_date=td,
            )
            if df is not None and df.height > 0:
                long_frames.append(df)

        merged_batch = _merge_and_dedupe(long_frames)
        logger.info("批次长表行数=%s", merged_batch.height)

        if merged_batch.height == 0:
            continue

        daily_dates = (
            merged_batch.filter(pl.col("source") == "daily_parquet")
            .select("trade_date")
            .unique()
            .to_series()
            .to_list()
        )
        all_daily_trade_dates.extend([str(x) for x in daily_dates if x is not None])

        wide_batch = _build_wide(merged_batch)
        logger.info("批次宽表行数=%s 列数=%s", wide_batch.height, len(wide_batch.columns))

        if wide_acc is None:
            wide_acc = wide_batch
        else:
            # 各批次因子列不重叠，按主键做横向合并。
            wide_acc = wide_acc.join(
                wide_batch,
                on=["stock_code", "trade_date"],
                how="full",
                coalesce=True,
            )

    if wide_acc is None:
        wide = pl.DataFrame(schema={"stock_code": pl.Utf8, "trade_date": pl.Utf8})
    else:
        wide = wide_acc.sort(by=["trade_date", "stock_code"])

    # 去重 source 文件列表，防止批次循环中重复记录。
    used_yearly_files = sorted(set(used_yearly_files))
    used_batch_files = sorted(set(used_batch_files))
    used_daily_files = sorted(set(used_daily_files))
    logger.info("宽表总行数=%s 列数=%s", wide.height, len(wide.columns))

    part_rel_paths = _write_month_partitions(
        wide_df=wide,
        output_root=output_root,
        universe=u,
        month=month,
        max_rows_per_part=max_rows_per_part,
    )

    as_of_trade_date = _calc_as_of_trade_date(wide)
    daily_max_trade_date = ""
    if include_daily and len(all_daily_trade_dates) > 0:
        daily_max_trade_date = str(max(all_daily_trade_dates))

    # manifest：按月一份
    manifest = {
        "schema_version": "v1",
        "universe": u,
        "month": month,
        "stage": stage,
        "as_of_trade_date": as_of_trade_date,
        "part_rel_paths": part_rel_paths,
        "yearly_source_count": len(used_yearly_files),
        "batch_source_count": len(used_batch_files),
        "daily_source_count": len(used_daily_files),
        "yearly_source_rel_paths": used_yearly_files,
        "batch_source_rel_paths": used_batch_files,
        "daily_source_rel_paths": used_daily_files,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "conflict_policy": "daily_parquet_over_yearly_parquet",
    }
    manifest_path = output_root / "meta" / "manifest" / "factor" / u / f"{month}.json"
    _write_json(manifest_path, manifest)

    # watermark：每域一份（只前进不回退）
    watermark = {
        "schema_version": "v1",
        "universe": u,
        "as_of_trade_date": as_of_trade_date,
        "month": month,
        "stage": stage,
        "batch_production_max_date": as_of_trade_date if (used_yearly_files or used_daily_files) else "",
        "daily_max_trade_date": daily_max_trade_date,
        "yearly_source_rel_paths": used_yearly_files,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "conflict_policy": "daily_parquet_over_yearly_parquet",
    }
    watermark_path = output_root / "meta" / "watermark" / "factor" / f"{u}.json"
    legacy_watermark_path = output_root / "meta" / "watermark" / f"{u}.json"
    existing_wm = _load_existing_watermark(watermark_path, legacy_path=legacy_watermark_path)
    old_as_of = _parse_iso_date(str(existing_wm.get("as_of_trade_date", "")))
    new_as_of = _parse_iso_date(as_of_trade_date)

    should_write_watermark = True
    if old_as_of is not None and new_as_of is not None and new_as_of < old_as_of:
        # 补跑历史月份时，不回退全域最新水位。
        should_write_watermark = False
        logger.info(
            "跳过 watermark 回退：existing_as_of=%s > new_as_of=%s（manifest 仍已更新）",
            old_as_of,
            new_as_of,
        )

    if should_write_watermark:
        _write_json(watermark_path, watermark)
    else:
        logger.info("保留已有 watermark=%s", watermark_path)

    logger.info(
        "导出完成 universe=%s month=%s parts=%s manifest=%s watermark=%s",
        u,
        month,
        len(part_rel_paths),
        manifest_path,
        watermark_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="按月导出对外训练 Parquet（候选阶段）")
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
        "--stage",
        default="candidate",
        choices=["candidate", "production", "deprecated"],
        help="读取 yearly_parquet 的 stage，当前建议 candidate",
    )
    parser.add_argument(
        "--include-daily",
        action="store_true",
        help="启用日更 patch（默认不启用）",
    )
    parser.add_argument(
        "--daily-recent-days",
        type=int,
        default=3,
        help="启用日更 patch 时，回看最近 N 个自然日（默认 3）",
    )
    parser.add_argument(
        "--factor-batch-size",
        type=int,
        default=50,
        help="按因子分批处理时每批因子数（默认 50，值越小内存峰值越低）",
    )
    parser.add_argument(
        "--max-rows-per-part",
        type=int,
        default=300000,
        help="每个 parquet part 的最大行数（默认 300000）",
    )
    parser.add_argument(
        "--output-root",
        default="",
        help="输出根目录，默认读 [factor_export].output_root",
    )
    args = parser.parse_args()

    run_export(
        config_file=args.config,
        universe=args.universe,
        month=args.month,
        stage=args.stage,
        include_daily=bool(args.include_daily),
        daily_recent_days=int(args.daily_recent_days),
        factor_batch_size=int(args.factor_batch_size),
        max_rows_per_part=int(args.max_rows_per_part),
        output_root_override=args.output_root.strip() or None,
    )


if __name__ == "__main__":
    main()

