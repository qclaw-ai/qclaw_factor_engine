#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
历史初始化回填脚本（bootstrap）：

- 按月调用 run_factor_export_parquet.py（默认 batch-only）
- 每个月导出后调用 validate_factor_export.py 做校验
- 输出成功/失败月份清单，便于重跑失败月
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

    # 这里用 date 做一次合法性校验。
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
    """
    执行子命令并实时输出日志。

    说明：
    - 不捕获 stdout/stderr，直接透传到终端，方便你实时看进度。
    - 失败由上层根据 returncode 处理。
    """
    return subprocess.run(cmd, cwd=str(cwd), check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="历史初始化回填：按月导出 parquet 并校验")
    parser.add_argument("--config", default="config.ini", help="根配置文件路径")
    parser.add_argument("--universe", required=True, help="如 ZZ500 / HS300")
    parser.add_argument("--start-month", required=True, help="起始月份 YYYY-MM")
    parser.add_argument("--end-month", required=True, help="结束月份 YYYY-MM")
    parser.add_argument("--stage", default="candidate", choices=["candidate", "production", "deprecated"], help="导出读取的 batch stage")
    parser.add_argument("--include-daily", action="store_true", help="是否在历史回填时启用 daily patch（默认关闭）")
    parser.add_argument("--daily-recent-days", type=int, default=3, help="include-daily 时回看最近 N 天")
    parser.add_argument("--factor-batch-size", type=int, default=50, help="导出时每批处理因子数量，默认 50")
    parser.add_argument("--max-rows-per-part", type=int, default=300000, help="每个 parquet part 最大行数")
    parser.add_argument("--output-root", default="artifacts/factor_export_parquet", help="导出根目录")
    parser.add_argument("--skip-validate", action="store_true", help="仅导出，不执行校验")
    parser.add_argument("--skip-reconcile", action="store_true", help="校验时跳过 CSV 抽样对账")
    parser.add_argument("--stop-on-error", action="store_true", help="某个月失败后立即停止（默认继续跑后续月份）")
    args = parser.parse_args()

    months = _iter_months(args.start_month, args.end_month)

    print(f"[BOOTSTRAP] universe={args.universe} stage={args.stage} months={months[0]}..{months[-1]} total={len(months)}")

    results: List[MonthRunResult] = []

    for month in months:
        print(f"\n[BOOTSTRAP] >>> 开始月份 {month}")

        export_cmd = [
            sys.executable,
            "src/factor_export_cos/factor_export_runner.py",
            "--config",
            args.config,
            "--universe",
            args.universe,
            "--month",
            month,
            "--stage",
            args.stage,
            "--factor-batch-size",
            str(args.factor_batch_size),
            "--max-rows-per-part",
            str(args.max_rows_per_part),
            "--output-root",
            args.output_root,
        ]
        if args.include_daily:
            export_cmd.extend(["--include-daily", "--daily-recent-days", str(args.daily_recent_days)])

        proc_export = _run_cmd(export_cmd, PROJECT_ROOT)
        if proc_export.returncode != 0:
            msg = f"export 失败，返回码={proc_export.returncode}"
            print(f"[BOOTSTRAP][FAIL] {month} {msg}")
            results.append(MonthRunResult(month=month, export_ok=False, validate_ok=False, error_message=msg))
            if args.stop_on_error:
                break
            continue

        validate_ok = True
        err = ""
        if not args.skip_validate:
            validate_cmd = [
                sys.executable,
                "scripts/validate_factor_export.py",
                "--output-root",
                args.output_root,
                "--universe",
                args.universe,
                "--month",
                month,
                "--project-root",
                ".",
            ]
            if args.skip_reconcile:
                validate_cmd.append("--skip-reconcile")

            proc_validate = _run_cmd(validate_cmd, PROJECT_ROOT)
            if proc_validate.returncode != 0:
                validate_ok = False
                err = f"validate 失败，返回码={proc_validate.returncode}"
                print(f"[BOOTSTRAP][FAIL] {month} {err}")
            else:
                print(f"[BOOTSTRAP][OK] {month} export + validate 通过")
        else:
            print(f"[BOOTSTRAP][OK] {month} export 通过（跳过校验）")

        results.append(MonthRunResult(month=month, export_ok=True, validate_ok=validate_ok, error_message=err))

        if args.stop_on_error and (not validate_ok):
            break

    # 汇总输出：给你一眼看到失败月份，便于重跑。
    ok_months = [r.month for r in results if r.export_ok and r.validate_ok]
    fail_months = [r.month for r in results if (not r.export_ok) or (not r.validate_ok)]

    print("\n[BOOTSTRAP] ===== 汇总 =====")
    print(f"[BOOTSTRAP] 成功月份({len(ok_months)}): {ok_months}")
    print(f"[BOOTSTRAP] 失败月份({len(fail_months)}): {fail_months}")

    if fail_months:
        print("[BOOTSTRAP] 失败详情:")
        for r in results:
            if (not r.export_ok) or (not r.validate_ok):
                print(f"  - {r.month}: {r.error_message or '未知错误'}")
        raise SystemExit(1)

    print("[BOOTSTRAP] 全部月份完成")


if __name__ == "__main__":
    main()

