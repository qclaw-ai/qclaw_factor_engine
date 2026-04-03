#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import redis
from sqlalchemy import text

# 把 common / backtest_core 加入路径，复用现有工具
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.db import get_db_manager
from common.factor_value_files_batch import batch_rel_path_to_abs, load_batch_csv_rel_paths
from common.universe_service import normalize_universe_code
from common.utils import setup_logger
from backtest_core.backtest_core_runner import _load_factor_csv


logger = setup_logger("factor_corr_matrix", "logs/factor_corr_matrix.log")


def _load_valid_factor_ids(db_config_file: str) -> List[str]:
    """从 factor_basic 中加载当前有效的因子列表（is_valid = true）"""
    db_manager = get_db_manager(config_file=db_config_file)
    session = db_manager.get_session()

    try:
        sql = text(
            """
            SELECT factor_id
            FROM factor_basic
            WHERE is_valid = TRUE
            ORDER BY factor_id
            """
        )
        rows = session.execute(sql).scalars().all()
        factor_ids: List[str] = list(rows)
        logger.info(f"从 factor_basic 加载到 {len(factor_ids)} 个 is_valid=true 的因子")
        return factor_ids
    finally:
        session.close()


def _find_batch_csv_by_directory_scan(
    factor_output_dir: str,
    factor_id: str,
    test_universe: str,
) -> str | None:
    """兼容：在目录下按文件名 end 日期选「最新」CSV（同 end 时仅按扫描顺序，不推荐与多版本并存）。"""
    if not os.path.isdir(factor_output_dir):
        logger.warning(f"因子输出目录不存在: {factor_output_dir}")
        return None

    candidates: List[Tuple[datetime, str]] = []
    u_tag = normalize_universe_code(test_universe)
    prefix = f"{factor_id}_{u_tag}_"

    for fname in os.listdir(factor_output_dir):
        if not fname.lower().endswith(".csv"):
            continue

        name_no_ext = os.path.splitext(fname)[0]
        if not name_no_ext.startswith(prefix):
            continue

        parts = name_no_ext.split("_")
        if len(parts) < 4:
            continue

        try:
            end_str = parts[-1]
            end_dt = datetime.strptime(end_str, "%Y-%m-%d")
        except Exception:
            # 文件名不符合 `<factor_id>_<UNIVERSE>_<start>_<end>.csv` 约定时跳过
            continue

        full_path = os.path.join(factor_output_dir, fname)
        candidates.append((end_dt, full_path))

    if not candidates:
        logger.warning(f"未在目录 {factor_output_dir} 下找到因子 {factor_id} 的 CSV 文件")
        return None

    candidates.sort(key=lambda x: x[0])
    latest_path = candidates[-1][1]
    logger.info(f"因子 {factor_id} 使用目录扫描选中的 CSV: {latest_path}")
    return latest_path


def _load_factor_series_for_window(
    config_file: str,
    project_root: str,
    factor_output_dir: str,
    factor_ids: List[str],
    start_date: datetime,
    end_date: datetime,
    test_universe: str,
    use_factor_value_files: bool,
) -> Dict[str, pd.Series]:
    """加载给定时间窗口内各因子的 factor_value 序列"""
    series_map: Dict[str, pd.Series] = {}

    u_norm = normalize_universe_code(test_universe)
    rel_map: Dict[str, str] = {}
    if use_factor_value_files:
        rel_map = load_batch_csv_rel_paths(
            config_file=config_file,
            universe=u_norm,
            factor_ids=factor_ids,
        )
        if not rel_map:
            logger.warning(
                "factor_value_files 未解析到任何 batch_csv（universe=%s），将无相关性输入",
                u_norm,
            )

    for fid in factor_ids:
        if use_factor_value_files:
            rel = rel_map.get(fid)
            if not rel:
                logger.warning(
                    "factor_value_files 无该因子 batch_csv，跳过: factor_id=%s",
                    fid,
                )
                continue

            csv_path = batch_rel_path_to_abs(project_root, rel)
            if not os.path.isfile(csv_path):
                logger.warning(
                    "因子 CSV 不存在（factor_value_files 指向路径无效）: factor_id=%s path=%s",
                    fid,
                    csv_path,
                )
                continue
        else:
            csv_path = _find_batch_csv_by_directory_scan(
                factor_output_dir=factor_output_dir,
                factor_id=fid,
                test_universe=test_universe,
            )
            if not csv_path:
                continue

        try:
            df = _load_factor_csv(csv_path)
        except Exception as e:
            logger.error(f"加载因子 CSV 失败, factor_id={fid}, path={csv_path}, err={e}")
            continue

        if "factor_value" not in df.columns:
            logger.warning(f"因子 CSV 缺少 factor_value 列, factor_id={fid}, path={csv_path}")
            continue

        s = df["factor_value"].copy()
        dates = s.index.get_level_values("trade_date")
        mask = (dates >= start_date) & (dates <= end_date)
        s = s[mask]

        if s.empty:
            logger.warning(
                f"因子 {fid} 在区间 {start_date.date()} ~ {end_date.date()} 内无有效数据"
            )
            continue

        series_map[fid] = s

    logger.info(f"窗口内成功加载 {len(series_map)} 个因子时间序列用于相关性计算")
    return series_map


def _build_corr_matrix(
    factor_series: Dict[str, pd.Series],
    min_overlap_days: int,
) -> Dict[str, Dict[str, float]]:
    """基于因子截面值构建相关性矩阵（Pearson），并按最小重叠天数过滤"""
    if not factor_series:
        return {}

    df = pd.DataFrame(factor_series)

    if df.empty:
        return {}

    corr_df = df.corr(method="pearson")

    result: Dict[str, Dict[str, float]] = defaultdict(dict)

    factor_ids = list(factor_series.keys())
    total_pairs = 0
    non_nan_pairs = 0
    overlap_pass_pairs = 0

    # 预先按日期聚合“该日哪些因子有值”，用于粗略估计重叠交易日
    idx = df.index
    trade_dates = idx.get_level_values("trade_date")
    by_date_avail: Dict[datetime, List[str]] = defaultdict(list)

    for col in factor_ids:
        col_series = df[col]
        mask = col_series.notna()
        for dt in trade_dates[mask]:
            by_date_avail[dt].append(col)

    overlap_days: Dict[Tuple[str, str], int] = defaultdict(int)
    for dt, col_list in by_date_avail.items():
        unique_cols = list(set(col_list))
        n = len(unique_cols)
        for i in range(n):
            for j in range(i + 1, n):
                a = unique_cols[i]
                b = unique_cols[j]
                key = (a, b)
                overlap_days[key] += 1

    for i in factor_ids:
        for j in factor_ids:
            if i == j:
                continue

            total_pairs += 1
            v = corr_df.at[i, j]
            if pd.isna(v):
                continue

            non_nan_pairs += 1
            key_pair = (i, j) if i < j else (j, i)
            days = overlap_days.get(key_pair, 0)

            if days < min_overlap_days:
                continue

            overlap_pass_pairs += 1
            result[i][j] = float(v)

    logger.info(
        "corr过滤统计: factor_count=%d, total_pairs=%d, non_nan_pairs=%d, "
        "overlap_pass_pairs=%d, min_overlap_days=%d",
        len(factor_ids),
        total_pairs,
        non_nan_pairs,
        overlap_pass_pairs,
        min_overlap_days,
    )

    return result


def _build_payload(
    as_of_date: datetime,
    window_days: int,
    corr_dict: Dict[str, Dict[str, float]],
    test_universe: str,
) -> Dict:
    """构建写入 Redis 的 JSON payload"""
    payload = {
        "as_of_date": as_of_date.strftime("%Y-%m-%d"),
        "test_universe": normalize_universe_code(test_universe),
        "window": f"{window_days}d",
        "corr": corr_dict,
        "generated_at": datetime.utcnow().isoformat(),
    }

    return payload


def _connect_redis(cfg: Config) -> redis.Redis:
    """根据 [redis] 配置段初始化 Redis 连接"""
    host = cfg.get("redis", "host", fallback="127.0.0.1")
    port_str = cfg.get("redis", "port", fallback="6379")
    db_str = cfg.get("redis", "db", fallback="0")
    password = cfg.get("redis", "password", fallback=None)

    try:
        port = int(port_str)
    except Exception:
        port = 6379

    try:
        db_idx = int(db_str)
    except Exception:
        db_idx = 0

    client = redis.Redis(
        host=host,
        port=port,
        db=db_idx,
        password=password,
        decode_responses=True,
    )

    return client


def _cleanup_old_keys(
    client: redis.Redis,
    prefix: str,
    test_universe: str,
    keep_days: int,
) -> None:
    """清理过旧的领域相关性 key，只保留最近 keep_days 个快照。"""
    u_tag = normalize_universe_code(test_universe)
    pattern = f"{prefix}:{u_tag}:*"
    keys = list(client.scan_iter(match=pattern))

    if not keys:
        return

    today = datetime.utcnow().date()
    keep_after = today - timedelta(days=keep_days)

    for key in keys:
        if key.endswith(":latest"):
            continue

        try:
            suffix = key.rsplit(":", 1)[-1]
            dt = datetime.strptime(suffix, "%Y%m%d").date()
        except Exception:
            continue

        if dt < keep_after:
            client.delete(key)
            logger.info(f"已删除过期相关性矩阵 key: {key}")


def run_factor_corr_matrix(config_file: str = "src/factor_corr/config.ini") -> None:
    """主入口：按调度周期计算相关性快照并写入 Redis。"""
    logger.info("启动 factor_corr_matrix 任务")

    cfg = Config(config_file=config_file)

    enable = cfg.getboolean("factor_corr", "enable", fallback=True)
    if not enable:
        logger.info("配置 factor_corr.enable = false，本次不执行相关性计算")
        return

    window_days = cfg.getint("factor_corr", "window_days", fallback=252)
    min_overlap_days = cfg.getint("factor_corr", "min_overlap_days", fallback=120)
    redis_key_prefix = cfg.get("factor_corr", "redis_key_prefix", fallback="factor:corr")
    keep_days = cfg.getint("factor_corr", "keep_days", fallback=30)
    test_universe = normalize_universe_code(
        cfg.get("factor_corr", "test_universe", fallback="ALL")
    )
    factor_output_root = cfg.get(
        "factor_corr",
        "factor_output_root",
        fallback="factor_values/by_universe",
    ).strip()
    factor_output_dir = os.path.join(factor_output_root, test_universe)
    use_factor_value_files = cfg.getboolean(
        "factor_corr",
        "use_factor_value_files",
        fallback=True,
    )
    project_root = str(Path(__file__).resolve().parents[2])

    as_of_str = cfg.get("factor_corr", "as_of_date", fallback="")
    if as_of_str:
        as_of_date = datetime.strptime(as_of_str, "%Y-%m-%d")
    else:
        as_of_date = datetime.utcnow()

    start_date = as_of_date - timedelta(days=window_days)
    logger.info(
        f"相关性计算窗口: {start_date.date()} ~ {as_of_date.date()}, "
        f"window_days={window_days}, min_overlap_days={min_overlap_days}, "
        f"test_universe={test_universe}, use_factor_value_files={use_factor_value_files}, "
        f"factor_output_dir={factor_output_dir}"
    )

    # 1) 加载有效因子列表
    valid_factor_ids = _load_valid_factor_ids(db_config_file=config_file)
    if not valid_factor_ids:
        logger.warning("未从 factor_basic 加载到任何 is_valid=true 的因子，结束任务")
        return

    factor_ids_raw = cfg.get("factor_corr", "factor_ids", fallback="").strip()
    include_factor_ids: List[str] = [
        fid.strip() for fid in factor_ids_raw.split(",") if fid.strip()
    ]
    if include_factor_ids:
        factor_ids = [fid for fid in valid_factor_ids if fid in include_factor_ids]
        logger.info(
            f"根据配置 factor_ids 过滤后，参与相关性计算的因子数量: {len(factor_ids)}"
        )
    else:
        factor_ids = valid_factor_ids

    if not factor_ids:
        logger.warning("参与相关性计算的因子列表为空，结束任务")
        return

    # 2) 加载时间窗口内的因子值序列
    factor_series = _load_factor_series_for_window(
        config_file=config_file,
        project_root=project_root,
        factor_output_dir=factor_output_dir,
        factor_ids=factor_ids,
        start_date=start_date,
        end_date=as_of_date,
        test_universe=test_universe,
        use_factor_value_files=use_factor_value_files,
    )
    if not factor_series:
        logger.warning("窗口内未加载到任何因子序列，结束任务")
        return

    # 3) 构建相关性矩阵并按最小重叠天数过滤
    corr_dict = _build_corr_matrix(
        factor_series=factor_series,
        min_overlap_days=min_overlap_days,
    )
    if not corr_dict:
        logger.warning("相关性矩阵为空（或全部因子对不满足最小重叠天数），结束任务")
        return

    # 4) 写入 Redis
    payload = _build_payload(
        as_of_date=as_of_date,
        window_days=window_days,
        corr_dict=corr_dict,
        test_universe=test_universe,
    )
    redis_client = _connect_redis(cfg)
    redis_key = f"{redis_key_prefix}:{test_universe}:{as_of_date.strftime('%Y%m%d')}"
    latest_key = f"{redis_key_prefix}:{test_universe}:latest"

    redis_client.set(redis_key, json.dumps(payload, ensure_ascii=False))
    redis_client.set(latest_key, json.dumps(payload, ensure_ascii=False))
    logger.info(f"相关性矩阵已写入 Redis, key={redis_key}")
    logger.info(f"相关性矩阵 latest 指针已更新, key={latest_key}")

    # 5) 清理旧 key
    if keep_days > 0:
        _cleanup_old_keys(
            client=redis_client,
            prefix=redis_key_prefix,
            test_universe=test_universe,
            keep_days=keep_days,
        )


def main():
    run_factor_corr_matrix()


if __name__ == "__main__":
    main()

