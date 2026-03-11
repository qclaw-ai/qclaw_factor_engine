#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict

# 把 common 模块加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.utils import setup_logger

from factor_crawler.fd_crawler import fetch_factors as fetch_fd_factors, RawFactor

logger = setup_logger("factor_crawler_runner", "logs/factor_crawler_runner.log")


SOURCE_PREFIX_MAP: Dict[str, str] = {
    "fd": "FD",
    "bq": "BQ",
    "jq": "JQ",
}


def _generate_factor_id(source_key: str, index: int, as_of: datetime | None = None) -> str:
    """根据来源与日期生成因子ID：FACTOR_<SRC>_<YYYYMMDD>_<序号>"""
    prefix = SOURCE_PREFIX_MAP.get(source_key, source_key.upper())
    d = as_of or datetime.now()
    date_str = d.strftime("%Y%m%d")
    return f"FACTOR_{prefix}_{date_str}_{index:03d}"


def _factor_to_md(factor_id: str, rf: RawFactor) -> str:
    """将 RawFactor 转为标准 Markdown 文本"""
    return (
        "# 因子说明文档\n\n"
        f"因子ID: {factor_id}  \n"
        f"因子名称: {rf.name}  \n"
        f"公式(DSL): {rf.raw_formula}  \n"
        f"描述: {rf.description}  \n"
        f"因子类型: {rf.type}  \n"
        f"适用股票池: {rf.universe}  \n"
        f"调仓周期: {rf.period}  \n"
        f"因子方向: {rf.direction}  \n"
        f"来源URL: {rf.url}\n"
    )


def _write_factor_md(
    docs_dir: str,
    source_key: str,
    factors: List[RawFactor],
    max_factors: int,
) -> List[str]:
    """将抓取到的 RawFactor 写入 Markdown 文件，返回生成的 factor_id 列表"""
    os.makedirs(docs_dir, exist_ok=True)
    created_ids: List[str] = []
    as_of = datetime.now()

    for idx, rf in enumerate(factors, start=1):
        if idx > max_factors:
            break
        factor_id = _generate_factor_id(source_key, idx, as_of=as_of)
        md_content = _factor_to_md(factor_id, rf)

        file_name = f"{factor_id}.md"
        path = os.path.join(docs_dir, file_name)

        with open(path, "w", encoding="utf-8") as f:
            f.write(md_content)

        created_ids.append(factor_id)
        logger.info("生成因子文档: %s -> %s", factor_id, path)

    return created_ids


def run_factor_crawler(config_file: str = "src/factor_crawler/config.ini") -> None:
    logger.info("启动 factor_crawler_runner")

    cfg = Config(config_file=config_file)

    sources_raw = cfg.get("crawler", "sources", fallback="fd").strip()
    source_keys = [s.strip() for s in sources_raw.split(",") if s.strip()]
    max_factors_per_run = cfg.getint("crawler", "max_factors_per_run", fallback=10)
    docs_dir = cfg.get("paths", "factor_docs_dir", fallback="factor_docs/md")

    logger.info(
        "配置 - sources=%s, max_factors_per_run=%s, factor_docs_dir=%s",
        source_keys,
        max_factors_per_run,
        docs_dir,
    )

    created_total: List[str] = []

    for src in source_keys:
        if src == "fd":
            base_url = cfg.get("sources.fd", "base_url", fallback="https://factors.directory")
            keywords_raw = cfg.get("sources.fd", "search_keywords", fallback="").strip()
            keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]

            logger.info("开始抓取 factors.directory，keywords=%s", keywords)
            raw_factors = fetch_fd_factors(base_url=base_url, search_keywords=keywords)

            if not raw_factors:
                logger.warning("factors.directory 暂未抓取到任何因子（当前适配器为占位实现）")
                continue

            ids = _write_factor_md(
                docs_dir=docs_dir,
                source_key="fd",
                factors=raw_factors,
                max_factors=max_factors_per_run,
            )
            created_total.extend(ids)

        else:
            logger.warning("数据源 %s 暂未实现适配器，跳过", src)

    logger.info("本次 factor_crawler 完成，生成因子数量: %d, ids=%s", len(created_total), created_total)


def main():
    run_factor_crawler()


if __name__ == "__main__":
    main()

