#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import re
from dataclasses import dataclass
from typing import List, Optional

# 对齐旧项目：把 common 模块加入路径
sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config
from common.utils import setup_logger

logger = setup_logger("factor_docs_parser", "logs/factor_docs_parser.log")


@dataclass
class FactorDefinition:
    factor_id: str
    factor_name: str
    formula: str
    description: str
    factor_type: str
    test_universe: str
    trading_cycle: str
    factor_direction: str
    source_url: str
    doc_path: str


def _extract_field(pattern: re.Pattern, text: str) -> Optional[str]:
    """从文本中按给定 regex 提取单行字段"""
    match = pattern.search(text)
    if not match:
        return None
    # 取第一个捕获组并 strip
    return match.group(1).strip()


def parse_factor_md(path: str) -> Optional[FactorDefinition]:
    """解析单个因子 Markdown 文档"""
    logger.info(f"解析因子文档: {path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"读取文件失败 {path}: {e}")
        return None

    # 支持类似「因子ID: xxx」或「因子ID：xxx」（中英文冒号）
    patterns = {
        "factor_id": re.compile(r"因子ID[:：]\s*(.+)"),
        "factor_name": re.compile(r"因子名称[:：]\s*(.+)"),
        # 优先使用「公式(DSL)」，若不存在再退回「公式」
        "formula_dsl": re.compile(r"公式\(DSL\)[:：]\s*(.+)"),
        "formula_raw": re.compile(r"公式[:：]\s*(.+)"),
        "description": re.compile(r"描述[:：]\s*(.+)"),
        "factor_type": re.compile(r"因子类型[:：]\s*(.+)"),
        "test_universe": re.compile(r"(?:适用股票池|测试股票池)[:：]\s*(.+)"),
        "trading_cycle": re.compile(r"(?:调仓周期|交易周期)[:：]\s*(.+)"),
        "factor_direction": re.compile(r"(?:因子方向|方向)[:：]\s*(.+)"),
        "source_url": re.compile(r"(?:来源URL|来源链接)[:：]\s*(.+)"),
    }

    raw_fields = {key: _extract_field(pat, content) for key, pat in patterns.items()}

    # 公式字段：优先用 DSL，没有的话退回原始公式
    formula = raw_fields.get("formula_dsl") or raw_fields.get("formula_raw")
    fields = {
        "factor_id": raw_fields.get("factor_id"),
        "factor_name": raw_fields.get("factor_name"),
        "formula": formula,
        "description": raw_fields.get("description"),
        "factor_type": raw_fields.get("factor_type"),
        "test_universe": raw_fields.get("test_universe"),
        "trading_cycle": raw_fields.get("trading_cycle"),
        "factor_direction": raw_fields.get("factor_direction"),
        "source_url": raw_fields.get("source_url"),
    }

    # 关键字段检查
    critical_keys = ["factor_id", "factor_name", "formula", "factor_direction"]
    missing_critical = [k for k in critical_keys if not fields.get(k)]
    if missing_critical:
        logger.error(f"因子文档 {path} 缺少关键字段: {missing_critical}，跳过该文件")
        return None

    # 非关键字段给默认值但打 warning
    defaults = {
        "description": "",
        "factor_type": "",
        "test_universe": "",
        "trading_cycle": "",
        "source_url": "",
    }
    for key, default in defaults.items():
        if not fields.get(key):
            logger.warning(f"因子文档 {path} 缺少字段 {key}，使用默认值")
            fields[key] = default

    # 标准化方向
    direction = fields["factor_direction"].lower()
    if direction not in ("long", "short"):
        logger.warning(
            f"因子文档 {path} 的因子方向值为 {fields['factor_direction']}，"
            f"不在 (long/short) 内，按 long 处理"
        )
        direction = "long"

    return FactorDefinition(
        factor_id=fields["factor_id"],
        factor_name=fields["factor_name"],
        formula=fields["formula"],
        description=fields["description"],
        factor_type=fields["factor_type"],
        test_universe=fields["test_universe"],
        trading_cycle=fields["trading_cycle"],
        factor_direction=direction,
        source_url=fields["source_url"],
        doc_path=os.path.abspath(path),
    )


def load_all_factors(config_file: str = "src/factor_docs/config.ini") -> List[FactorDefinition]:
    """扫描 factor_docs_dir 下所有 md，解析为因子定义列表"""
    cfg = Config(config_file=config_file)
    docs_dir = cfg.get("paths", "factor_docs_dir", fallback="factor_docs/md")

    logger.info(f"扫描因子文档目录: {docs_dir}")

    if not os.path.isdir(docs_dir):
        logger.error(f"因子文档目录不存在: {docs_dir}")
        return []

    factors: List[FactorDefinition] = []

    for root, _, files in os.walk(docs_dir):
        for name in files:
            if not name.lower().endswith(".md"):
                continue
            full_path = os.path.join(root, name)
            fd = parse_factor_md(full_path)
            if fd is not None:
                factors.append(fd)

    logger.info(f"共解析到 {len(factors)} 个因子定义")
    return factors


def main():
    """简单 CLI：解析并打印所有因子定义"""
    logger.info("启动 factor_docs_parser 脚本")
    factors = load_all_factors()
    for f in factors:
        logger.info(
            f"因子: {f.factor_id} | 名称: {f.factor_name} | 股票池: {f.test_universe} "
            f"| 周期: {f.trading_cycle} | 方向: {f.factor_direction}"
        )


if __name__ == "__main__":
    main()

