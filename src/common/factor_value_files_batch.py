#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_value_files：批量因子 CSV（artifact_type=batch_csv）路径解析。

与 qclaw_strategy_engine 策略侧约定一致：同一 (factor_id, universe) 下取
created_at 最新、id 最大的一条（PostgreSQL DISTINCT ON）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy import text

from common.db import get_db_manager
from common.universe_service import normalize_universe_code


def load_batch_csv_rel_paths(
    config_file: str,
    universe: str,
    factor_ids: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    从 factor_value_files 读取 batch_csv 相对路径（POSIX，相对仓库根）。

    :param factor_ids: 若非空，仅解析这些因子；为空则返回该 universe 下全部 batch_csv 的当前版本。
    :return: factor_id -> rel_path
    """
    u = normalize_universe_code(universe)
    db_manager = get_db_manager(config_file=config_file)
    session = db_manager.get_session()

    try:
        if factor_ids:
            sql = text(
                """
                SELECT DISTINCT ON (factor_id)
                    factor_id, rel_path
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'batch_csv'
                  AND factor_id = ANY(:factor_ids)
                ORDER BY factor_id, created_at DESC, id DESC
                """
            )
            rows = session.execute(
                sql,
                {"universe": u, "factor_ids": list(factor_ids)},
            ).fetchall()
        else:
            sql = text(
                """
                SELECT DISTINCT ON (factor_id)
                    factor_id, rel_path
                FROM factor_value_files
                WHERE universe = :universe
                  AND artifact_type = 'batch_csv'
                ORDER BY factor_id, created_at DESC, id DESC
                """
            )
            rows = session.execute(sql, {"universe": u}).fetchall()
    finally:
        session.close()

    out: Dict[str, str] = {}
    for r in rows:
        fid = str(r[0]).strip()
        rp = str(r[1]).strip() if r[1] is not None else ""
        if fid and rp:
            out[fid] = rp

    return out


def batch_rel_path_to_abs(project_root: str, rel_path: str) -> str:
    """仓库根 + POSIX 相对路径 -> 本机绝对路径。"""
    rel = (rel_path or "").strip().replace("/", os.sep)
    if not rel:
        return ""

    return str((Path(project_root) / rel).resolve())
