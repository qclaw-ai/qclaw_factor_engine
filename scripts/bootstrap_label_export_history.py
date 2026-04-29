#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Label 单独历史回填脚本（按月）：

- 调用 src/factor_export_cos/label_export_runner.py（stock_daily → y_ret_1d/y_ret_5d）
- 每月导出后可选执行 scripts/validate_label_export.py

与 bootstrap_factor_export_history.py（因子导出）互不依赖。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import List


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MonthRunResult:
    month: str
    export_ok: bool
    validate_ok: bool
    error_message: str = ""


def _month_add_one(ym: str) -> str:
    """YYYY-MM 向后推一个月。"""
    y, m = ym.split("-")
    yy = int(y)
    mm = int(m)
    if mm == 12:
        return f"{yy + 1:04d}-01"
    return f"{yy:04d}-{mm + 1:02d}"


def _iter_months(start_month: str, end_month: str) -> List[str]:
    """闭区间枚举月份，格式 YYYY-MM。"""
    if len(start_month) != 7 or len(end_month) != 7:
        raise ValueError("start_month / end_month 需为 YYYY-MM")

    _ = date.fromisoformat(start_month + "-01")
    _ = date.fromisoformat(end_month + "-01")

    if start_month > end_month:
        raise ValueError(f"start_month 不能晚于 end_month: {start_month} > {end_month}")

    out: List[str] = []
    cur = start_month
    while cur <= end_month:
        out.append(cur)
        cur = _month_add_one(cur)
    return out


def _run_cmd(cmd: List[str], cwd: Path) -> subprocess.CompletedProcess:
    """执行子命令并实时透传日志。"""
    return subprocess.run(cmd, cwd=str(cwd), check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="历史回填：仅按月导出 label（y_ret_1d）并可选校验")
    parser.add_argument("--config", default="config.ini", help="根配置文件路径")
    parser.add_argument("--universe", required=True, help="如 ZZ500 / HS300 / ALL")
    parser.add_argument("--start-month", required=True, help="起始月份 YYYY-MM")
    parser.add_argument("--end-month", required=True, help="结束月份 YYYY-MM")
    parser.add_argument("--max-rows-per-part", type=int, default=500_000, help="每个 parquet part 最大行数")
    parser.add_argument(
        "--sql-end-buffer-days",
        type=int,
        default=45,
        help="label_export SQL 末端相对月末的缓冲日历天（传给 run_label_export_parquet）",
    )
    parser.add_argument("--output-root", default="artifacts/factor_export_parquet", help="导出根目录")
    parser.add_argument("--skip-validate", action="store_true", help="仅导出，不校验")
    parser.add_argument("--stop-on-error", action="store_true", help="某月失败后立即停止")
    args = parser.parse_args()

    months = _iter_months(args.start_month, args.end_month)

    print(
        f"[BOOTSTRAP-LABEL] universe={args.universe} months={months[0]}..{months[-1]} total={len(months)} "
        f"output_root={args.output_root}",
    )

    results: List[MonthRunResult] = []

    for month in months:
        print(f"\n[BOOTSTRAP-LABEL] >>> 月份 {month}")

        export_cmd = [
            sys.executable,
            "src/factor_export_cos/label_export_runner.py",
            "--config",
            args.config,
            "--universe",
            args.universe,
            "--month",
            month,
            "--max-rows-per-part",
            str(args.max_rows_per_part),
            "--sql-end-buffer-days",
            str(args.sql_end_buffer_days),
            "--output-root",
            args.output_root,
        ]

        proc_export = _run_cmd(export_cmd, PROJECT_ROOT)
        if proc_export.returncode != 0:
            msg = f"export 失败 returncode={proc_export.returncode}"
            print(f"[BOOTSTRAP-LABEL][FAIL] {month} {msg}")
            results.append(MonthRunResult(month=month, export_ok=False, validate_ok=False, error_message=msg))
            if args.stop_on_error:
                break
            continue

        validate_ok = True
        err_msg = ""

        if not args.skip_validate:
            validate_cmd = [
                sys.executable,
                "scripts/validate_label_export.py",
                "--root",
                args.output_root,
                "--universe",
                args.universe,
                "--month",
                month,
            ]
            proc_v = _run_cmd(validate_cmd, PROJECT_ROOT)
            if proc_v.returncode != 0:
                validate_ok = False
                err_msg = f"validate 失败 returncode={proc_v.returncode}"
                print(f"[BOOTSTRAP-LABEL][FAIL] {month} {err_msg}")
            else:
                print(f"[BOOTSTRAP-LABEL][OK] {month} export + validate 通过")
        else:
            print(f"[BOOTSTRAP-LABEL][OK] {month} export（跳过校验）")

        results.append(MonthRunResult(month=month, export_ok=True, validate_ok=validate_ok, error_message=err_msg))

        if args.stop_on_error and not validate_ok:
            break

    ok_months = [r.month for r in results if r.export_ok and r.validate_ok]
    fail_months = [r.month for r in results if not r.export_ok or not r.validate_ok]

    print("\n[BOOTSTRAP-LABEL] ===== 汇总 =====")
    print(f"[BOOTSTRAP-LABEL] 成功月份({len(ok_months)}): {ok_months}")
    print(f"[BOOTSTRAP-LABEL] 失败月份({len(fail_months)}): {fail_months}")

    if fail_months:
        print("[BOOTSTRAP-LABEL] 失败详情:")
        for r in results:
            if not r.export_ok or not r.validate_ok:
                print(f"  - {r.month}: {r.error_message or '校验未通过或未执行'}")
        raise SystemExit(1)

    print("[BOOTSTRAP-LABEL] 全部月份完成")


if __name__ == "__main__":
    main()
