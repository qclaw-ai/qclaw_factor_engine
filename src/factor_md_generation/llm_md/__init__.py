# -*- coding: utf-8 -*-
"""
LLM 生成标准因子 Markdown（模板对齐 `factor_docs/md`）。

配置：`src/factor_md_generation/config*.ini`
CLI：`python src/factor_md_generation/llm_md/cli.py`
"""

from .llm_builder import (
    FactorLlmRecord,
    generate_factor_md_from_text,
    write_factor_md_file,
)
from .canonical import FactorMdCanonical, render_factor_md_content

__all__ = [
    "FactorLlmRecord",
    "FactorMdCanonical",
    "generate_factor_md_from_text",
    "render_factor_md_content",
    "write_factor_md_file",
]
