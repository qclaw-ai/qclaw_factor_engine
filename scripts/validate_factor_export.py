#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

import polars as pl


def _load_json(path: Path) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _month_bounds(month: str) -> Tuple[str, str]:
    y, m = month.split("-")
    y = int(y)
    m = int(m)
    if m == 12:
        next_y, next_m = y + 1, 1
    else:
        next_y, next_m = y, m + 1

    start = f"{y:04d}-{m:02d}-01"
    # 通过日期字符串比较即可，end 用下月第一天前一天简单推导
    # 这里只用于校验，不依赖复杂日历库
    import datetime as _dt
    end_dt = _dt.date(next_y, next_m, 1) - _dt.timedelta(days=1)
    end = end_dt.strftime("%Y-%m-%d")
    return start, end


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _check_manifest_and_watermark(
    root: Path,
    universe: str,
    month: str,
) -> Tuple[Dict, Dict, List[Path]]:
    # 优先读取新路径（带 factor 标识），并兼容历史旧路径。
    manifest_path = root / "meta" / "manifest" / "factor" / universe / f"{month}.json"
    if not manifest_path.exists():
        manifest_path = root / "meta" / "manifest" / universe / f"{month}.json"

    watermark_path = root / "meta" / "watermark" / "factor" / f"{universe}.json"
    if not watermark_path.exists():
        watermark_path = root / "meta" / "watermark" / f"{universe}.json"

    _assert(manifest_path.exists(), f"manifest 不存在: {manifest_path}")
    _assert(watermark_path.exists(), f"watermark 不存在: {watermark_path}")

    manifest = _load_json(manifest_path)
    watermark = _load_json(watermark_path)

    _assert(manifest.get("universe") == universe, "manifest.universe 不匹配")
    _assert(manifest.get("month") == month, "manifest.month 不匹配")
    _assert("part_rel_paths" in manifest and len(manifest["part_rel_paths"]) > 0, "manifest.part_rel_paths 为空")
    _assert("as_of_trade_date" in watermark and str(watermark["as_of_trade_date"]).strip() != "", "watermark.as_of_trade_date 为空")

    part_paths = [root / p for p in manifest["part_rel_paths"]]
    for p in part_paths:
        _assert(p.exists(), f"manifest 指向文件不存在: {p}")

    return manifest, watermark, part_paths


def _check_parquet_shape_and_keys(part_paths: List[Path], month: str) -> pl.DataFrame:
    df = pl.read_parquet([str(p) for p in part_paths])
    _assert(df.height > 0, "parquet 数据为空")
    _assert("stock_code" in df.columns, "缺少列 stock_code")
    _assert("trade_date" in df.columns, "缺少列 trade_date")

    # 月份范围检查
    min_d, max_d = (
        df.select(
            [
                pl.col("trade_date").min().alias("min_trade_date"),
                pl.col("trade_date").max().alias("max_trade_date"),
            ]
        )
        .row(0)
    )
    start, end = _month_bounds(month)
    _assert(str(min_d) >= start and str(max_d) <= end, f"trade_date 超出月份范围: [{min_d}, {max_d}] vs [{start}, {end}]")

    # 主键重复检查
    dup_cnt = (
        df.group_by(["stock_code", "trade_date"])
        .len()
        .filter(pl.col("len") > 1)
        .height
    )
    _assert(dup_cnt == 0, f"发现重复主键 (stock_code, trade_date): {dup_cnt}")

    return df


def _sample_reconcile_with_csv(
    project_root: Path,
    manifest: Dict,
    df_wide: pl.DataFrame,
    sample_rows: int,
    tolerance: float,
) -> None:
    """
    用 manifest 第一条 batch CSV 做抽样对账：
    - 从 csv 抽 sample_rows
    - 与宽表对应 factor 列比对
    """
    batch_sources = manifest.get("batch_source_rel_paths", [])
    if not batch_sources:
        print("[WARN] manifest 无 batch_source_rel_paths，跳过 CSV 抽样对账")
        return

    csv_rel = str(batch_sources[0]).strip()
    csv_path = (project_root / csv_rel).resolve()
    if not csv_path.exists():
        print(f"[WARN] 对账 CSV 不存在，跳过: {csv_path}")
        return

    # 从文件名提 factor_id: {factor_id}_{universe}_{start}_{end}.csv
    # 右侧固定 3 段，剩余左侧拼回 factor_id
    stem = csv_path.stem
    parts = stem.split("_")
    if len(parts) < 4:
        print(f"[WARN] CSV 命名不符合约定，跳过对账: {csv_path.name}")
        return
    factor_col = "_".join(parts[:-3])
    if factor_col not in df_wide.columns:
        print(f"[WARN] 宽表未找到因子列 {factor_col}，跳过对账")
        return

    raw = pl.read_csv(str(csv_path))
    if "trade_date" not in raw.columns and "date" in raw.columns:
        raw = raw.rename({"date": "trade_date"})
    needed = {"stock_code", "trade_date", "factor_value"}
    if not needed.issubset(set(raw.columns)):
        print(f"[WARN] CSV 缺少必要列 {needed}，跳过对账")
        return

    month = str(manifest.get("month", "")).strip()
    _assert(month, "manifest.month 为空，无法做 CSV 抽样按月过滤")
    month_start, month_end = _month_bounds(month)

    raw = (
        raw.with_columns(pl.col("trade_date").cast(pl.Utf8).str.slice(0, 10).alias("trade_date"))
        .filter((pl.col("trade_date") >= month_start) & (pl.col("trade_date") <= month_end))
    )
    _assert(raw.height > 0, f"CSV 在目标月份 {month} 内无数据，无法抽样对账")

    sample = raw.select(["stock_code", "trade_date", "factor_value"]).head(sample_rows)

    joined = (
        sample.join(
            df_wide.select(["stock_code", "trade_date", factor_col]).rename({factor_col: "factor_value_pq"}),
            on=["stock_code", "trade_date"],
            how="left",
        )
        .with_columns((pl.col("factor_value") - pl.col("factor_value_pq")).abs().alias("abs_diff"))
    )

    miss = joined.filter(pl.col("factor_value_pq").is_null()).height
    max_diff = joined.select(pl.col("abs_diff").max()).item()
    if max_diff is None:
        max_diff = 0.0

    _assert(miss == 0, f"CSV 抽样对账命中缺失 {miss} 行")
    _assert(float(max_diff) <= tolerance, f"CSV 抽样最大误差超阈值: {max_diff} > {tolerance}")

    print(f"[OK] CSV 抽样对账通过 factor={factor_col} sample_rows={sample_rows} max_abs_diff={max_diff}")


def main() -> None:
    parser = argparse.ArgumentParser(description="校验 factor_export 产物（manifest/watermark/parquet/抽样对账）")
    parser.add_argument("--output-root", default="artifacts/factor_export_parquet", help="导出根目录")
    parser.add_argument("--universe", required=True, help="如 ZZ500")
    parser.add_argument("--month", required=True, help="如 2026-04")
    parser.add_argument("--project-root", default=".", help="仓库根目录，用于 CSV 抽样对账")
    parser.add_argument("--sample-rows", type=int, default=20, help="CSV 抽样对账行数")
    parser.add_argument("--tolerance", type=float, default=1e-8, help="浮点容差")
    parser.add_argument("--skip-reconcile", action="store_true", help="跳过 CSV 抽样对账")
    args = parser.parse_args()

    out_root = Path(args.output_root).resolve()
    project_root = Path(args.project_root).resolve()

    manifest, watermark, part_paths = _check_manifest_and_watermark(out_root, args.universe, args.month)
    print(f"[OK] manifest/watermark 校验通过, parts={len(part_paths)}")
    print(f"[INFO] watermark.as_of_trade_date={watermark.get('as_of_trade_date')}")

    wide_df = _check_parquet_shape_and_keys(part_paths, args.month)
    print(f"[OK] parquet 结构/主键/日期范围校验通过, rows={wide_df.height}, cols={len(wide_df.columns)}")

    if not args.skip_reconcile:
        _sample_reconcile_with_csv(
            project_root=project_root,
            manifest=manifest,
            df_wide=wide_df,
            sample_rows=args.sample_rows,
            tolerance=args.tolerance,
        )

    print("[PASS] 全部校验通过")


if __name__ == "__main__":
    main()

