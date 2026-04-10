# -*- coding: utf-8 -*-
"""
因子 Markdown 生成与落盘（LLM / 手动 / 未来 FastAPI）。

- 配置统一放在包外层：`src/factor_md_generation/config*.ini`（`[paths]` + `[llm_gateway]`）
- LLM 流水线：`factor_md_generation.llm_md`
- 手写/表单保存：`factor_md_generation.manual_md`
"""

from factor_md_generation.io_paths import (
    DEFAULT_FACTOR_MD_CONFIG_FILE,
    FACTOR_MD_LLM_SUBDIR,
    FACTOR_MD_MANUAL_SUBDIR,
    project_root,
    resolve_factor_docs_dir,
    resolve_llm_md_output_dir,
    resolve_manual_md_output_dir,
)

__all__ = [
    "DEFAULT_FACTOR_MD_CONFIG_FILE",
    "FACTOR_MD_LLM_SUBDIR",
    "FACTOR_MD_MANUAL_SUBDIR",
    "project_root",
    "resolve_factor_docs_dir",
    "resolve_llm_md_output_dir",
    "resolve_manual_md_output_dir",
]
