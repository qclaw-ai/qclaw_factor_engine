#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
`factor_md_generation` 包内统一：配置文件路径、项目根、`factor_docs_dir` 解析。

统一读取仓库根 `config*.ini`，供：
- `llm_md`（LLM 生成）
- `manual_md`（表单/接口手写保存）
- 后续 FastAPI 服务共用。
"""

from __future__ import annotations

import os
from pathlib import Path

import sys

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from common.config import Config

# 与 `common.Config` 约定一致：非 prod 自动切到 config_dev.ini
DEFAULT_FACTOR_MD_CONFIG_FILE = "config.ini"

# 相对 `paths.factor_docs_dir` 下的子目录（LLM / 手写 分仓落盘）
FACTOR_MD_LLM_SUBDIR = "llm"
FACTOR_MD_MANUAL_SUBDIR = "manual"


def project_root() -> Path:
    """本文件位于 `src/factor_md_generation/io_paths.py` → 项目根为 parents[2]。"""

    return Path(__file__).resolve().parents[2]


def resolve_factor_docs_dir(*, config_file: str = DEFAULT_FACTOR_MD_CONFIG_FILE) -> Path:
    """从配置读取 `paths.factor_docs_dir`（相对项目根）并解析为绝对路径。"""

    cfg = Config(config_file=config_file)
    rel = cfg.get("paths", "factor_docs_dir", fallback="factor_docs/md")
    root = project_root()
    if os.path.isabs(rel):
        return Path(rel).resolve()
    return (root / rel).resolve()


def resolve_llm_md_output_dir(*, config_file: str = DEFAULT_FACTOR_MD_CONFIG_FILE) -> Path:
    """LLM 生成 Markdown 默认目录：`factor_docs_dir/llm`。"""

    return resolve_factor_docs_dir(config_file=config_file) / FACTOR_MD_LLM_SUBDIR


def resolve_manual_md_output_dir(*, config_file: str = DEFAULT_FACTOR_MD_CONFIG_FILE) -> Path:
    """手写/表单保存 Markdown 默认目录：`factor_docs_dir/manual`。"""

    return resolve_factor_docs_dir(config_file=config_file) / FACTOR_MD_MANUAL_SUBDIR
