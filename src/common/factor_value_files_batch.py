#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_value_files：批量因子 Parquet 路径解析（yearly_parquet）。

``yearly_parquet`` 按 (factor_id, year) 维度 DISTINCT ON；回测/矩阵按因子聚合为按年排序的路径列表。
"""

from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy import text

from common.db import get_db_manager
from common.universe_service import normalize_universe_code


def load_yearly_parquet_rel_paths_grouped_by_factor(
    config_file: str,
    universe: str,
    factor_ids: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """
    从 ``factor_value_files`` 读取 ``yearly_parquet``，按 ``factor_id`` 聚合为 **按年升序** 的 ``rel_path`` 列表。

    :return: ``factor_id`` -> ``[rel_path_year1, rel_path_year2, ...]``
    """
    flat = load_yearly_parquet_rel_paths(
        config_file=config_file,
        universe=universe,
        factor_ids=factor_ids,
    )
    tmp: Dict[str, List[Tuple[int, str]]] = defaultdict(list)

    for (fid, yr), rp in flat.items():
        if fid and rp:
            tmp[fid].append((int(yr), rp))

    return {
        fid: [rp for _, rp in sorted(lst, key=lambda x: x[0])]
        for fid, lst in tmp.items()
        if lst
    }


def is_parquet_factor_rel_path(rel_path: str) -> bool:
    """判断 ``rel_path`` 是否指向 Parquet（如 ``yearly_parquet`` 主存）。"""
    s = (rel_path or "").strip().lower()
    return s.endswith(".parquet")


def load_yearly_parquet_rel_paths(
    config_file: str,
    universe: str,
    factor_ids: Optional[List[str]] = None,
) -> Dict[Tuple[str, int], str]:
    """
    从 factor_value_files 读取 ``yearly_parquet`` 相对路径（POSIX，相对仓库根）。

    :param factor_ids: 若非空，仅解析这些因子；为空则返回该 universe 下全部 yearly 行。
    :return: ``(factor_id, year)`` -> ``rel_path``
    """
    u = normalize_universe_code(universe)
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        if factor_ids:
            sql = text(
                """
                SELECT DISTINCT ON (factor_id, year)
                    factor_id, year, rel_path
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'yearly_parquet'
                  AND year IS NOT NULL
                  AND rel_path IS NOT NULL
                  AND rel_path <> ''
                  AND factor_id = ANY(:factor_ids)
                ORDER BY factor_id, year, updated_at DESC, created_at DESC, id DESC
                """
            )
            rows = session.execute(
                sql,
                {"universe": u, "factor_ids": list(factor_ids)},
            ).fetchall()
        else:
            sql = text(
                """
                SELECT DISTINCT ON (factor_id, year)
                    factor_id, year, rel_path
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'yearly_parquet'
                  AND year IS NOT NULL
                  AND rel_path IS NOT NULL
                  AND rel_path <> ''
                ORDER BY factor_id, year, updated_at DESC, created_at DESC, id DESC
                """
            )
            rows = session.execute(sql, {"universe": u}).fetchall()
    finally:
        session.close()

    out: Dict[Tuple[str, int], str] = {}
    for r in rows:
        fid = str(r[0]).strip()
        yr_raw = r[1]
        rp = str(r[2]).strip() if r[2] is not None else ""
        if not fid or yr_raw is None or not rp:
            continue
        try:
            y_int = int(yr_raw)
        except (TypeError, ValueError):
            continue
        out[(fid, y_int)] = rp

    return out


def batch_rel_path_to_abs(project_root: str, rel_path: str) -> str:
    """仓库根 + POSIX 相对路径 -> 本机绝对路径。"""
    rel = (rel_path or "").strip().replace("/", os.sep)
    if not rel:
        return ""

    return str((Path(project_root) / rel).resolve())
