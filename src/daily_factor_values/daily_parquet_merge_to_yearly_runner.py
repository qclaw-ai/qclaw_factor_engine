#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 3：从日更多因子 ``factors.parquet`` 回补年度单因子 Parquet，并 upsert ``factor_value_files``（yearly_parquet）。

- **不调用**日更计算入口；只读已存在的 ``factor_values_parquet/daily/by_universe/.../factors.parquet``。
- 回补失败可**单独重跑本脚本**（合并语义与 ``factor_engine_runner`` 内 ``_merge_write_yearly_parquet_long`` 一致，幂等）。
- 个人维护：参数集中在 ``main()``，业务在 ``run_daily_parquet_merge_to_yearly``。

**边界（行为约定，非脚本能单独「补全」）**

- **当年尚无 yearly 文件**：合并写盘会新建 ``{factor_id}/{factor_id}-{year}.parquet``，属正常；DB 由 upsert 插入新行。
- **年文件已有，但最后一笔 trade_date 比本次 bundle 早很多**：合并只追加**本次日更截面**；中间若从未跑过日更/回补，年文件里会长期存在**交易日空洞**——应用侧应靠「按序跑 daily + 本脚本」或定期 **batch 重算** 填洞。脚本用 ``--gap-warning-calendar-days`` 检测日历间隔；可加 ``--halt-on-calendar-gap`` 在超过阈值时 **直接退出（2）**、不写盘不改库，避免「看见大洞仍硬补」。
- **DB 里 ``date_end`` 落后**：只要本脚本成功 upsert，会按合并后 parquet 的 min/max 刷新；若从不跑回补，DB 与盘会一起旧。
- **bundle 与目录名 ``trade_date`` 不一致**：仍以 parquet 内 ``trade_date`` 参与按年分组；目录名仅用于定位文件（与阶段 2 约定一致）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd
import polars as pl
from sqlalchemy import text

# 对齐：把 src 加入路径（common / factor_engine）
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.universe_service import normalize_universe_code
from common.utils import setup_logger
from factor_engine.factor_engine_runner import (
    _daily_parquet_bundle_path,
    _merge_write_yearly_parquet_long,
    _upsert_factor_value_file_yearly_parquet,
)

logger = setup_logger(
    "daily_parquet_merge_to_yearly_runner",
    "logs/daily_parquet_merge_to_yearly_runner.log",
)


def _project_root() -> Path:
    """本文件位于 ``src/daily_factor_values``，向上两级为仓库根。"""
    return Path(__file__).resolve().parents[2]


def _parse_factor_ids_csv(raw: str) -> Set[str]:
    return {x.strip() for x in raw.split(",") if x.strip()}


def _discover_bundle_trade_dates(
    project_root: Path,
    universe: str,
    d0: date,
    d1: date,
) -> List[date]:
    """
    列出 daily bundle 目录下、闭区间 [d0, d1] 内且存在 ``factors.parquet`` 的 trade_date（升序）。

    路径约定与 ``factor_engine_runner._daily_parquet_bundle_path`` 一致。
    """
    base = project_root / "factor_values_parquet" / "daily" / "by_universe" / universe
    if not base.is_dir():
        logger.warning("未发现 daily universe 目录（可能尚无日更产物）path=%s", base)
        return []

    out: List[date] = []

    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        name = child.name

        try:
            td = date.fromisoformat(name[:10])
        except ValueError:
            continue

        if td < d0 or td > d1:
            continue

        pq = child / "factors.parquet"

        if pq.is_file():
            out.append(td)

    return out


def _manifest_batch_id(bundle_dir: Path) -> Optional[str]:
    """读取同目录 ``manifest.json`` 的 ``batch_id``（若无或损坏则返回 None）。"""
    mf = bundle_dir / "manifest.json"

    if not mf.is_file():
        return None

    try:
        with open(mf, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("读取 manifest 失败 path=%s err=%s", mf, e)
        return None

    raw = data.get("batch_id")

    if raw is None:
        return None

    s = str(raw).strip()
    return s or None


def _load_factor_names(session, factor_ids: Sequence[str]) -> Dict[str, str]:
    """``factor_id`` -> ``factor_name``；库中无行时调用方可用 factor_id 占位。"""
    ids = sorted({str(x).strip() for x in factor_ids if str(x).strip()})

    if not ids:
        return {}

    rows = session.execute(
        text(
            """
            SELECT factor_id, factor_name
            FROM factor_basic
            WHERE factor_id = ANY(:factor_ids)
            """
        ),
        {"factor_ids": ids},
    ).fetchall()

    out: Dict[str, str] = {}

    for r in rows:
        fid = str(r[0]).strip()
        nm = str(r[1]).strip() if r[1] is not None else ""
        out[fid] = nm if nm else fid

    return out


def _read_yearly_max_trade_date(
    project_root: Path,
    universe: str,
    factor_id: str,
    year: int,
) -> Optional[date]:
    """
    若磁盘上已有该年 yearly parquet（新路径 ``.../{factor_id}/{factor_id}-{year}.parquet`` 优先，否则旧扁平路径），
    返回其中 ``trade_date`` 最大值；不存在或读失败则 None。
    """
    universe_dir = (
        project_root
        / "factor_values_parquet"
        / "yearly"
        / "by_universe"
        / universe
    )
    fname = f"{factor_id}-{year}.parquet"
    nested = universe_dir / factor_id / fname
    flat = universe_dir / fname

    path: Optional[Path] = None

    if nested.is_file():
        path = nested
    elif flat.is_file():
        path = flat

    if path is None:
        return None

    try:
        mx = (
            pl.read_parquet(str(path), columns=["trade_date"])
            .select(pl.col("trade_date").max())
            .to_series()[0]
        )
    except Exception as e:
        logger.warning(
            "读取 yearly parquet 的 trade_date 最大值失败 path=%s err=%s",
            path,
            e,
        )
        return None

    if mx is None:
        return None

    return date.fromisoformat(str(mx)[:10])


def _evaluate_calendar_gap_breach(
    *,
    bundle_trade_date: date,
    project_root: Path,
    universe: str,
    factor_id: str,
    year: int,
    gap_warning_calendar_days: int,
) -> Optional[Tuple[date, int]]:
    """
    当已有 yearly 的最大 trade_date 早于本次 bundle 所属日，且日历间隔超过阈值时，
    返回 ``(yearly_max_trade_date, gap_calendar_days)``；否则 ``None``。

    用于发现「年文件尾部落后很多天」——往往表示中间缺少 daily bundle 或未按序回补。
    """
    if gap_warning_calendar_days <= 0:
        return None

    old_max = _read_yearly_max_trade_date(project_root, universe, factor_id, year)

    if old_max is None:
        return None

    if old_max >= bundle_trade_date:
        return None

    delta = (bundle_trade_date - old_max).days

    if delta > gap_warning_calendar_days:
        return (old_max, delta)

    return None


def run_daily_parquet_merge_to_yearly(
    *,
    config_file: str,
    universe: str,
    trade_dates: Sequence[date],
    factor_ids_filter: Optional[Set[str]],
    dry_run: bool,
    batch_id_override: Optional[str],
    gap_warning_calendar_days: int = 14,
    halt_on_calendar_gap: bool = False,
) -> None:
    """
    对多个交易日顺序执行：读 bundle → 按因子、日历年合并写 yearly → upsert 索引。

    :param config_file: 根配置（含 ``[database]``）；``stage`` / ``is_rebase`` 从 ``[factor_incremental].factor_engine_config_file`` 的 ``[factor_engine]`` 读取。
    :param batch_id_override: 非空则覆盖 manifest / 默认 ``daily_merge{yyyymmdd}``。
    :param gap_warning_calendar_days: 与已有 yearly 的 ``max(trade_date)`` 相对本次 bundle 日的日历间隔超过该值时打 WARNING；``halt_on_calendar_gap`` 为真时则 ``sys.exit(2)``；``0`` 关闭检测。
    :param halt_on_calendar_gap: 为真且触发上述间隔条件时，立即退出进程码 2，不执行 merge/upsert。
    """
    u = normalize_universe_code(universe)
    project_root = _project_root()

    cfg_root = Config(config_file=config_file)
    fe_ini = cfg_root.get(
        "factor_incremental",
        "factor_engine_config_file",
        fallback="config.ini",
    )
    cfg_fe = Config(config_file=fe_ini)
    stage = (
        (cfg_fe.get("factor_engine", "stage", fallback="candidate") or "candidate")
        .strip()
        .lower()
    )
    is_rebase = bool(cfg_fe.getboolean("factor_engine", "is_rebase", fallback=False))

    if stage not in ("candidate", "production", "deprecated"):
        raise ValueError(f"配置错误：factor_engine.stage 不合法（{stage}）")

    dates_sorted = sorted({d for d in trade_dates})

    if not dates_sorted:
        logger.warning("trade_dates 为空，退出")
        return

    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        for td in dates_sorted:
            iso = td.isoformat()
            pq_path = _daily_parquet_bundle_path(project_root, u, iso)

            if not pq_path.is_file():
                logger.warning(
                    "跳过：不存在 factors.parquet universe=%s trade_date=%s path=%s",
                    u,
                    iso,
                    pq_path,
                )
                continue

            try:
                pl_df = pl.read_parquet(str(pq_path))
            except Exception as e:
                logger.error("读取 factors.parquet 失败 path=%s err=%s", pq_path, e)
                continue

            required = {"trade_date", "stock_code", "factor_id", "factor_value"}
            if not required <= set(pl_df.columns):
                logger.error(
                    "列不满足要求 path=%s cols=%s 需要包含 %s",
                    pq_path,
                    list(pl_df.columns),
                    sorted(required),
                )
                continue

            fids = [str(x) for x in pl_df["factor_id"].unique().to_list() if x is not None]
            names = _load_factor_names(session, fids)
            bundle_dir = pq_path.parent
            eff_batch = (
                (batch_id_override or _manifest_batch_id(bundle_dir) or f"daily_merge_{td.strftime('%Y%m%d')}")
                .strip()
            )

            logger.info(
                "回补 trade_date=%s universe=%s factors=%s batch_id=%s dry_run=%s halt_on_calendar_gap=%s",
                iso,
                u,
                len(fids),
                eff_batch,
                dry_run,
                halt_on_calendar_gap,
            )

            for fid in fids:
                if factor_ids_filter is not None and fid not in factor_ids_filter:
                    continue

                sub = pl_df.filter(pl.col("factor_id") == fid)

                if sub.height == 0:
                    continue

                pdf = sub.drop("factor_id").to_pandas()
                pdf["trade_date"] = pd.to_datetime(pdf["trade_date"])
                factor_name = names.get(fid, fid)

                for year_key, grp in pdf.groupby(pdf["trade_date"].dt.year, sort=True):
                    g = grp[["trade_date", "stock_code", "factor_value"]].copy()
                    y_int = int(year_key)

                    breach = _evaluate_calendar_gap_breach(
                        bundle_trade_date=td,
                        project_root=project_root,
                        universe=u,
                        factor_id=fid,
                        year=y_int,
                        gap_warning_calendar_days=gap_warning_calendar_days,
                    )

                    if breach is not None:
                        old_max, delta = breach

                        if halt_on_calendar_gap:
                            logger.error(
                                "检测到日历大洞（超过 gap_warning_calendar_days=%s），"
                                "已按 halt_on_calendar_gap 终止：未执行 merge/upsert。"
                                "请先补跑中间日更 bundle 或做 batch 区间重算。"
                                " universe=%s factor_id=%s year=%s bundle_trade_date=%s yearly_max_trade_date=%s gap_calendar_days=%s",
                                gap_warning_calendar_days,
                                u,
                                fid,
                                y_int,
                                td.isoformat(),
                                old_max.isoformat(),
                                delta,
                            )
                            sys.exit(2)

                        logger.warning(
                            "yearly 与本次 bundle 之间日历间隔较大（可能缺多日 daily 未回补）："
                            "universe=%s factor_id=%s year=%s bundle_trade_date=%s yearly_max_trade_date=%s gap_calendar_days=%s",
                            u,
                            fid,
                            y_int,
                            td.isoformat(),
                            old_max.isoformat(),
                            delta,
                        )

                    if dry_run:
                        logger.info(
                            "dry_run trade_date=%s factor_id=%s year=%s rows=%s",
                            iso,
                            fid,
                            y_int,
                            len(g),
                        )
                        continue

                    try:
                        rel_p, ds_d, de_d = _merge_write_yearly_parquet_long(
                            project_root=project_root,
                            universe=u,
                            factor_id=fid,
                            year=y_int,
                            df_new=g,
                        )

                        _upsert_factor_value_file_yearly_parquet(
                            session,
                            factor_id=fid,
                            factor_name=factor_name,
                            universe=u,
                            rel_path=rel_p,
                            year=y_int,
                            date_start=ds_d,
                            date_end=de_d,
                            batch_id=eff_batch,
                            stage=stage,
                            is_rebase=is_rebase,
                        )
                        session.commit()
                        logger.info(
                            "yearly_parquet 已回补 trade_date=%s factor_id=%s year=%s rel_path=%s",
                            iso,
                            fid,
                            y_int,
                            rel_p,
                        )
                    except Exception as e:
                        session.rollback()
                        logger.error(
                            "回补失败 trade_date=%s factor_id=%s year=%s err=%s",
                            iso,
                            fid,
                            y_int,
                            e,
                        )

    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="阶段3：日更 factors.parquet → yearly_parquet + DB 索引（可单独重跑、无需重跑日更）"
    )
    parser.add_argument(
        "--config",
        default="config.ini",
        help="根配置（含 [database]）；与 daily_factor_values_runner 一致",
    )
    parser.add_argument(
        "--universe",
        default="",
        help="与 bundle 目录一致（如 ZZ500）；默认读根配置 [daily].universe",
    )
    parser.add_argument(
        "--trade-date",
        default="",
        help="单个交易日 YYYY-MM-DD；与 --from-date/--to-date 二选一",
    )
    parser.add_argument(
        "--from-date",
        default="",
        help="批量：扫描目录闭区间起点 YYYY-MM-DD（需同时给 --to-date）",
    )
    parser.add_argument(
        "--to-date",
        default="",
        help="批量：闭区间终点 YYYY-MM-DD",
    )
    parser.add_argument(
        "--factor-ids",
        default="",
        help="可选：逗号分隔，仅处理这些 factor_id",
    )
    parser.add_argument(
        "--batch-id",
        default="",
        help="可选：覆盖写入 factor_value_files 的 batch_id（默认 manifest.batch_id 或 daily_mergeYYYYMMDD）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打日志，不写盘、不改库",
    )
    parser.add_argument(
        "--gap-warning-calendar-days",
        type=int,
        default=14,
        help="已有 yearly 的 max(trade_date) 早于本次 bundle 所属日时，若日历间隔超过该值则 WARNING；0 关闭检测（默认 14）",
    )
    parser.add_argument(
        "--halt-on-calendar-gap",
        action="store_true",
        help="与 --gap-warning-calendar-days 同一判定；触发时不写盘、不改库，立即以退出码 2 结束（适合 cron 显式失败）",
    )

    args = parser.parse_args()
    cfg = Config(config_file=args.config)
    universe_raw = args.universe.strip() or cfg.get("daily", "universe", fallback="ALL").strip()
    u = normalize_universe_code(universe_raw)

    filt: Optional[Set[str]] = None

    if args.factor_ids.strip():
        filt = _parse_factor_ids_csv(args.factor_ids)

    batch_override = args.batch_id.strip() or None

    td_single = args.trade_date.strip()
    d_from = args.from_date.strip()
    d_to = args.to_date.strip()

    trade_dates: List[date] = []

    if td_single:
        if d_from or d_to:
            parser.error("--trade-date 与 --from-date/--to-date 不能同时使用")
        trade_dates.append(date.fromisoformat(td_single))
    else:
        if bool(d_from) ^ bool(d_to):
            parser.error("--from-date 与 --to-date 需同时给出，或改用 --trade-date")

        if not d_from:
            parser.error("请指定 --trade-date，或同时指定 --from-date/--to-date")

        d0 = date.fromisoformat(d_from)
        d1 = date.fromisoformat(d_to)

        if d0 > d1:
            parser.error("--from-date 不能晚于 --to-date")

        trade_dates = _discover_bundle_trade_dates(_project_root(), u, d0, d1)

        if not trade_dates:
            logger.warning(
                "区间内未发现 factors.parquet universe=%s from=%s to=%s",
                u,
                d_from,
                d_to,
            )
            return

    run_daily_parquet_merge_to_yearly(
        config_file=args.config,
        universe=u,
        trade_dates=trade_dates,
        factor_ids_filter=filt,
        dry_run=bool(args.dry_run),
        batch_id_override=batch_override,
        gap_warning_calendar_days=int(args.gap_warning_calendar_days),
        halt_on_calendar_gap=bool(args.halt_on_calendar_gap),
    )


if __name__ == "__main__":
    main()
