#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将因子字段渲染为与 `factor_docs/md/FACTOR_DEMO_001.md` 一致的 Markdown 文本。

下游解析见 `factor_docs.factor_docs_parser`。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FactorMdCanonical:
    """与标准因子 MD 模板一一对应的字段。"""

    factor_id: str
    factor_name: str
    formula_original: str
    formula_dsl: str
    description: str
    factor_type: str
    applicable_stock_pool: str
    rebalance_cycle: str
    factor_direction: str
    source_url: str


def render_factor_md_content(f: FactorMdCanonical) -> str:
    """
    生成 `.md` 正文（末尾保留一个空行，与 DEMO 文件风格一致）。
    """

    # 与 FACTOR_DEMO_001 对齐：「描述 / 来源URL」在为空时仍保留冒号后的空格
    desc_line = "描述: " + f.description.strip()
    url_line = "来源URL: " + f.source_url.strip()

    lines = [
        f"因子ID: {f.factor_id.strip()} ",
        f"因子名称: {f.factor_name.strip()}  ",
        f"公式原文: {f.formula_original.strip()}  ",
        f"公式(DSL): {f.formula_dsl.strip()}  ",
        desc_line,
        f"因子类型: {f.factor_type.strip()}  ",
        f"适用股票池: {f.applicable_stock_pool.strip()} ",
        f"调仓周期: {f.rebalance_cycle.strip()}  ",
        f"因子方向: {f.factor_direction.strip()}  ",
        url_line,
        "",
        "",
    ]
    return "\n".join(lines)
