#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动（表单/JSON）保存因子 Markdown：校验 DSL + 可选解析闭环，再写入 `factor_docs_dir`。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Union

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from common.utils import setup_logger
from factor_docs.factor_docs_parser import parse_factor_md_content
from factor_engine.factor_dsl_allowlist import validate_factor_dsl_formula
from factor_md_generation.io_paths import (
    DEFAULT_FACTOR_MD_CONFIG_FILE,
    resolve_manual_md_output_dir,
)
from factor_md_generation.llm_md.canonical import FactorMdCanonical, render_factor_md_content
from factor_md_generation.llm_md.llm_builder import write_factor_md_file

logger = setup_logger("manual_factor_md", "logs/manual_factor_md.log")


def _canonical_from_payload(payload: Dict[str, Any]) -> FactorMdCanonical:
    """将前端/调用方字典转为 FactorMdCanonical（字段名与 LLM JSON 对齐）。"""

    required = [
        "factor_id",
        "factor_name",
        "formula_original",
        "formula_dsl",
        "factor_type",
        "factor_direction",
    ]
    missing = [k for k in required if not str(payload.get(k, "")).strip()]
    if missing:
        raise ValueError(f"缺少必填字段: {missing}")

    direction = str(payload["factor_direction"]).strip().lower()
    if direction not in ("long", "short"):
        raise ValueError("factor_direction 必须是 long 或 short")

    return FactorMdCanonical(
        factor_id=str(payload["factor_id"]).strip(),
        factor_name=str(payload["factor_name"]).strip(),
        formula_original=str(payload["formula_original"]).strip(),
        formula_dsl=str(payload["formula_dsl"]).strip(),
        description=str(payload.get("description") or "").strip(),
        factor_type=str(payload["factor_type"]).strip(),
        applicable_stock_pool=str(payload.get("applicable_stock_pool") or "ALL").strip(),
        rebalance_cycle=str(payload.get("rebalance_cycle") or "日线").strip(),
        factor_direction=direction,
        source_url=str(payload.get("source_url") or "").strip(),
    )


def save_manual_factor_md(
    payload: Dict[str, Any],
    *,
    output_path: Optional[Path] = None,
    config_file: str = DEFAULT_FACTOR_MD_CONFIG_FILE,
    validate_dsl: bool = True,
    validate_parse: bool = True,
    write_sidecar_meta: bool = False,
) -> Path:
    """
    保存手动填写的因子为标准 `.md`。

    :param payload: 与模板字段一致的字典（可来自 FastAPI Body）
    :param output_path: 绝对/相对路径；默认写入 `factor_docs_dir / {factor_id}.md`
    :param validate_dsl: 是否做 DSL 白名单静态校验
    :param validate_parse: 是否用 `parse_factor_md_content` 做闭环
    :return: 写入路径
    """

    canonical = _canonical_from_payload(payload)

    if validate_dsl:
        validate_factor_dsl_formula(canonical.formula_dsl)

    md = render_factor_md_content(canonical)

    if validate_parse:
        parsed = parse_factor_md_content(md, doc_path=str(output_path or canonical.factor_id))
        if parsed is None:
            raise ValueError("生成 Markdown 未通过 parse_factor_md_content 校验")

    if output_path is None:
        out_dir = resolve_manual_md_output_dir(config_file=config_file)
        safe_id = "".join(c for c in canonical.factor_id if c.isalnum() or c in "._-")
        if not safe_id:
            raise ValueError("factor_id 无效，无法生成文件名")
        output_path = out_dir / f"{safe_id}.md"

    meta: Optional[Dict[str, Any]] = None
    if write_sidecar_meta:
        meta = {
            "source": "manual",
            "payload": payload,
            "config_file": config_file,
        }

    path = write_factor_md_file(
        md_content=md,
        output_path=Path(output_path),
        llm_meta=meta,
        write_sidecar_meta=write_sidecar_meta,
    )

    logger.info("手动保存因子文档: %s", path)
    return path


def save_manual_factor_md_from_json_file(path: Union[str, Path], **kwargs: Any) -> Path:
    """从 JSON 文件读取 payload 并保存（便于脚本/测试）。"""

    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("JSON 根必须是对象")
    return save_manual_factor_md(data, **kwargs)
