#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""校验按月导出的 label Parquet（y_ret_1d）与 manifest / watermark。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = month.split("-")
    y = int(y)
    m = int(m)
    if m == 12:
        next_y, next_m = y + 1, 1
    else:
        next_y, next_m = y, m + 1

    start = f"{y:04d}-{m:02d}-01"
    import datetime as _dt

    end_dt = _dt.date(next_y, next_m, 1) - _dt.timedelta(days=1)
    end = end_dt.strftime("%Y-%m-%d")
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser(description="校验 label Parquet + meta")
    parser.add_argument("--root", required=True, help="导出根目录（含 label/、meta/）")
    parser.add_argument("--universe", required=True, help="如 ZZ500")
    parser.add_argument("--month", required=True, help="YYYY-MM")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    universe = args.universe.strip()
    month = args.month.strip()

    manifest_path = root / "meta" / "manifest" / "label" / universe / f"{month}.json"
    wm_path = root / "meta" / "watermark" / "label" / f"{universe}.json"

    _assert(manifest_path.exists(), f"manifest 不存在: {manifest_path}")
    manifest = _load_json(manifest_path)
    _assert(manifest.get("universe") == universe, "manifest.universe")
    _assert(manifest.get("month") == month, "manifest.month")

    paths = [root / p for p in manifest.get("part_rel_paths", [])]
    _assert(len(paths) > 0, "part_rel_paths 为空")

    for p in paths:
        _assert(p.exists(), f"part 缺失: {p}")

    df = pl.read_parquet([str(x) for x in paths])
    _assert(df.height > 0, "Parquet 无行")
    _assert(
        {"stock_code", "trade_date", "y_ret_1d", "y_ret_5d"} <= set(df.columns),
        "列不完整（需含 y_ret_1d、y_ret_5d）",
    )

    start, end = _month_bounds(month)
    rng = df.select(
        [
            pl.col("trade_date").min().alias("min_td"),
            pl.col("trade_date").max().alias("max_td"),
        ]
    ).row(0)
    min_td, max_td = str(rng[0]), str(rng[1])
    _assert(min_td >= start and max_td <= end, f"trade_date 超出月范围 [{min_td}, {max_td}]")

    dup_cnt = (
        df.group_by(["stock_code", "trade_date"]).len().filter(pl.col("len") > 1).height
    )
    _assert(dup_cnt == 0, f"(stock_code, trade_date) 重复 {dup_cnt} 条")

    if wm_path.exists():
        wm = _load_json(wm_path)
        _assert(
            str(wm.get("universe", "")).strip() == universe,
            "watermark.universe",
        )

    print("OK", manifest_path.relative_to(root))


if __name__ == "__main__":
    main()
