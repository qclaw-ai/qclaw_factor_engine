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
from typing import Any, Dict, List, Optional

import polars as pl
from sqlalchemy import text

# 对齐仓库其他 runner：将 src 目录加入路径，便于导入 common.*
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.universe_service import normalize_universe_code
from common.utils import setup_logger

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


@dataclass
class DailySourceRow:
    factor_id: str
    rel_path: str
    trade_date: str
    created_at: str
    row_id: int


@dataclass
class FactorBatchGroup:
    """按 factor_id 分组后的批次输入。"""
    factor_ids: List[str]
    batch_rows: List[BatchSourceRow]
    daily_rows: List[DailySourceRow]


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


def _fetch_batch_sources(
    config_file: str,
    universe: str,
    stage: str,
    month_start: date,
    month_end: date,
) -> List[BatchSourceRow]:
    """从 factor_value_files 查询指定月份有交集的 batch_csv 文件。"""
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()
    try:
        rows = session.execute(
            text(
                """
                SELECT
                    factor_id,
                    rel_path,
                    date_start,
                    date_end,
                    COALESCE(is_rebase, FALSE) AS is_rebase,
                    COALESCE(created_at, CURRENT_TIMESTAMP) AS created_at,
                    id
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'batch_csv'
                  AND stage = :stage
                  AND rel_path IS NOT NULL
                  AND rel_path <> ''
                  AND date_start IS NOT NULL
                  AND date_end IS NOT NULL
                  AND date_start <= :month_end
                  AND date_end >= :month_start
                ORDER BY factor_id, is_rebase DESC, created_at DESC, id DESC
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
        out.append(
            BatchSourceRow(
                factor_id=str(r["factor_id"]).strip(),
                rel_path=str(r["rel_path"]).strip(),
                date_start=str(r["date_start"]),
                date_end=str(r["date_end"]),
                is_rebase=bool(r["is_rebase"]),
                created_at=str(r["created_at"]),
                row_id=int(r["id"]),
            )
        )
    return out


def _fetch_daily_sources(
    config_file: str,
    universe: str,
    month_start: date,
    month_end: date,
    recent_days: int,
) -> List[DailySourceRow]:
    """从 factor_value_files 查询 daily_csv 文件（默认只取最近 N 天）。"""
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()
    try:
        if recent_days > 0:
            # 默认只 patch 最近 N 天，降低每天运行量。
            window_start = month_end - timedelta(days=recent_days - 1)
            effective_start = max(month_start, window_start)
        else:
            effective_start = month_start

        rows = session.execute(
            text(
                """
                SELECT
                    factor_id,
                    rel_path,
                    trade_date,
                    COALESCE(created_at, CURRENT_TIMESTAMP) AS created_at,
                    id
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'daily_csv'
                  AND rel_path IS NOT NULL
                  AND rel_path <> ''
                  AND trade_date IS NOT NULL
                  AND trade_date >= :date_start
                  AND trade_date <= :date_end
                ORDER BY factor_id, trade_date, created_at DESC, id DESC
                """
            ),
            {
                "universe": universe,
                "date_start": effective_start,
                "date_end": month_end,
            },
        ).mappings().all()
    finally:
        session.close()

    out: List[DailySourceRow] = []
    for r in rows:
        out.append(
            DailySourceRow(
                factor_id=str(r["factor_id"]).strip(),
                rel_path=str(r["rel_path"]).strip(),
                trade_date=str(r["trade_date"]),
                created_at=str(r["created_at"]),
                row_id=int(r["id"]),
            )
        )
    return out


def _read_factor_csv(
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
    读取单个因子 CSV 并标准化为长表。

    标准列：
    - stock_code
    - trade_date
    - factor_id
    - factor_value
    - source_priority / is_rebase / created_at / row_id（去重排序辅助列）
    """
    if not abs_path.exists():
        logger.warning("CSV 不存在，跳过: %s", abs_path)
        return None

    try:
        df = pl.read_csv(str(abs_path))
    except Exception as e:
        logger.error("读取 CSV 失败 path=%s err=%s", abs_path, e)
        return None

    if df.height == 0:
        return None

    # 兼容历史字段名：date -> trade_date
    cols = set(df.columns)
    if "trade_date" not in cols and "date" in cols:
        df = df.rename({"date": "trade_date"})

    required = {"stock_code", "trade_date", "factor_value"}
    if not required.issubset(set(df.columns)):
        logger.warning(
            "CSV 缺少必需列，跳过 path=%s need=%s got=%s",
            abs_path,
            sorted(required),
            df.columns,
        )
        return None

    # 仅保留目标月份，解决“不规则区间 CSV”问题。
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

    冲突优先级（由低到高）：
    1) batch 非 rebase
    2) batch rebase
    3) daily（你已拍板：同键 daily 覆盖 batch）
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
    batch_rows: List[BatchSourceRow],
    daily_rows: List[DailySourceRow],
    factor_batch_size: int,
) -> List[FactorBatchGroup]:
    """
    按 factor_id 切批，降低内存峰值。

    规则：
    - 以 batch/daily 中出现过的因子并集作为全集
    - 每批最多 factor_batch_size 个因子
    """
    if factor_batch_size <= 0:
        raise ValueError(f"factor_batch_size 必须 > 0，实际={factor_batch_size}")

    factor_ids = sorted(
        set([r.factor_id for r in batch_rows] + [r.factor_id for r in daily_rows])
    )
    if not factor_ids:
        return []

    batch_map: Dict[str, List[BatchSourceRow]] = {}
    for r in batch_rows:
        batch_map.setdefault(r.factor_id, []).append(r)

    daily_map: Dict[str, List[DailySourceRow]] = {}
    for r in daily_rows:
        daily_map.setdefault(r.factor_id, []).append(r)

    groups: List[FactorBatchGroup] = []
    start = 0
    while start < len(factor_ids):
        chunk = factor_ids[start:start + factor_batch_size]
        b_rows: List[BatchSourceRow] = []
        d_rows: List[DailySourceRow] = []
        for fid in chunk:
            b_rows.extend(batch_map.get(fid, []))
            d_rows.extend(daily_map.get(fid, []))

        groups.append(
            FactorBatchGroup(
                factor_ids=chunk,
                batch_rows=b_rows,
                daily_rows=d_rows,
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

    - batch 来源：factor_value_files.batch_csv + stage
    - daily 来源：factor_value_files.daily_csv（可选）
    - 冲突规则：同键 daily 覆盖 batch
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
    batch_rows = _fetch_batch_sources(
        config_file=config_file,
        universe=u,
        stage=stage,
        month_start=month_start,
        month_end=month_end,
    )
    logger.info("命中 batch_csv 源文件记录数=%s", len(batch_rows))

    daily_rows: List[DailySourceRow] = []
    if include_daily:
        daily_rows = _fetch_daily_sources(
            config_file=config_file,
            universe=u,
            month_start=month_start,
            month_end=month_end,
            recent_days=daily_recent_days,
        )
    logger.info("命中 daily_csv 源文件记录数=%s", len(daily_rows))

    source_groups = _group_sources_by_factor(
        batch_rows=batch_rows,
        daily_rows=daily_rows,
        factor_batch_size=int(factor_batch_size),
    )
    logger.info("因子分批完成 batch_count=%s", len(source_groups))

    wide_acc: Optional[pl.DataFrame] = None
    used_batch_files: List[str] = []
    used_daily_files: List[str] = []
    all_daily_trade_dates: List[str] = []

    for idx, grp in enumerate(source_groups, start=1):
        logger.info(
            "处理因子批次 %s/%s factor_count=%s",
            idx,
            len(source_groups),
            len(grp.factor_ids),
        )

        long_frames: List[pl.DataFrame] = []

        # 先加载 batch，source_priority=0
        for r in grp.batch_rows:
            abs_path = _resolve_abs_path(project_root, r.rel_path)
            df = _read_factor_csv(
                abs_path=abs_path,
                factor_id=r.factor_id,
                universe=u,
                month_start=month_start,
                month_end=month_end,
                source="batch",
                source_priority=0,
                is_rebase=1 if r.is_rebase else 0,
                created_at=r.created_at,
                row_id=r.row_id,
            )
            if df is not None and df.height > 0:
                long_frames.append(df)
                used_batch_files.append(r.rel_path)

        # 再加载 daily，source_priority=1（保证覆盖 batch）
        for r in grp.daily_rows:
            abs_path = _resolve_abs_path(project_root, r.rel_path)
            df = _read_factor_csv(
                abs_path=abs_path,
                factor_id=r.factor_id,
                universe=u,
                month_start=month_start,
                month_end=month_end,
                source="daily",
                source_priority=1,
                is_rebase=0,
                created_at=r.created_at,
                row_id=r.row_id,
            )
            if df is not None and df.height > 0:
                long_frames.append(df)
                used_daily_files.append(r.rel_path)

        merged_batch = _merge_and_dedupe(long_frames)
        logger.info("批次长表行数=%s", merged_batch.height)

        if merged_batch.height == 0:
            continue

        daily_dates = (
            merged_batch.filter(pl.col("source") == "daily")
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
        "batch_source_count": len(used_batch_files),
        "daily_source_count": len(used_daily_files),
        "batch_source_rel_paths": used_batch_files,
        "daily_source_rel_paths": used_daily_files,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "conflict_policy": "daily_override_batch",
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
        "batch_production_max_date": as_of_trade_date if used_batch_files else "",
        "daily_max_trade_date": daily_max_trade_date,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "conflict_policy": "daily_override_batch",
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
        help="读取 batch_csv 的 stage，当前建议 candidate",
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

