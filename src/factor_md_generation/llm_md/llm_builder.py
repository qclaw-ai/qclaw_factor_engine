#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 生成因子 JSON → 渲染为标准因子 Markdown（与 FACTOR_DEMO_001 一致）。

formula_dsl 在落盘前会经 `factor_engine.factor_dsl_allowlist.validate_factor_dsl_formula` 静态校验。
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from common.utils import setup_logger
from factor_engine.factor_dsl_allowlist import (
    build_llm_dsl_allowlist_payload,
    build_llm_dsl_constraints_text,
    validate_factor_dsl_formula,
)

from .canonical import FactorMdCanonical, render_factor_md_content
from .json_extract import extract_first_json_object
from .llm_client import LlmGatewayClient

logger = setup_logger("factor_md_llm_builder", "logs/factor_md_llm_builder.log")


class FactorLlmRecord(BaseModel):
    """模型输出的单条因子记录（字段名英文化，便于 JSON 稳定）。"""

    factor_id: Optional[str] = Field(default=None, description="因子ID，如 FACTOR_XXX；可空由程序生成")
    factor_name: str = Field(..., description="因子名称（英文或中文短名）")
    formula_original: str = Field(
        ...,
        description="公式原文：可保留大写字段如 CLOSE/VOLUME，与研报表述一致",
    )
    formula_dsl: str = Field(
        ...,
        description="公式(DSL)：仅允许 factor_engine.factor_dsl_allowlist 中的字段与函数",
    )
    description: str = Field(default="", description="因子逻辑说明，可为空")
    factor_type: str = Field(..., description="因子类型，如 量价/动量/价值 等")
    applicable_stock_pool: str = Field(default="ALL", description="适用股票池")
    rebalance_cycle: str = Field(default="日线", description="调仓周期")
    factor_direction: str = Field(default="long", description="long 或 short")
    source_url: str = Field(default="", description="来源链接，可为空")

    @field_validator("factor_direction")
    @classmethod
    def _norm_direction(cls, v: str) -> str:
        s = (v or "").strip().lower()
        if s not in ("long", "short"):
            raise ValueError("factor_direction 必须是 long 或 short")
        return s


def _generate_factor_id() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = random.randint(1000, 9999)
    return f"FACTOR_{ts}_{suffix}"


def _slug_name(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    s = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "", s)
    return s[:64] if s else "unnamed_factor"


def build_factor_extraction_messages(
    *,
    user_text: str,
    forced_factor_id: Optional[str] = None,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    """构造 chat messages 与用于落盘 meta 的 user 负载摘要。"""

    system = (
        "你是量化因子工程师。请根据用户提供的材料，抽取或推导一个因子定义。"
        "你必须只输出一个 JSON 对象，不要输出任何多余文本或 Markdown。"
        + build_llm_dsl_constraints_text()
    )

    schema = {
        "factor_id": "string | null，若无法确定可填 null",
        "factor_name": "string",
        "formula_original": "string",
        "formula_dsl": "string",
        "description": "string，可为空字符串",
        "factor_type": "string",
        "applicable_stock_pool": "string，默认 ALL",
        "rebalance_cycle": "string，默认 日线",
        "factor_direction": "string，仅允许 long 或 short",
        "source_url": "string，可为空字符串",
    }

    user_obj: Dict[str, Any] = {
        "instruction": "输出单个因子定义的 JSON（不要数组）。",
        "user_material": user_text,
        "output_json_schema": schema,
    }
    user_obj.update(build_llm_dsl_allowlist_payload())

    if forced_factor_id:
        user_obj["forced_factor_id"] = forced_factor_id
        user_obj["note"] = "factor_id 必须使用 forced_factor_id 的值。"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_obj, ensure_ascii=False)},
    ]

    return messages, user_obj


def factor_record_from_llm_dict(data: Dict[str, Any]) -> FactorLlmRecord:
    """将 JSON dict 校验为强类型记录。"""

    return FactorLlmRecord.model_validate(data)


def to_canonical(record: FactorLlmRecord, *, forced_factor_id: Optional[str] = None) -> FactorMdCanonical:
    """LLM 记录 → 标准 MD 字段。"""

    fid = (forced_factor_id or record.factor_id or "").strip()
    if not fid:
        fid = _generate_factor_id()

    name = record.factor_name.strip()
    if not name:
        name = _slug_name(fid)

    return FactorMdCanonical(
        factor_id=fid,
        factor_name=name,
        formula_original=record.formula_original.strip(),
        formula_dsl=record.formula_dsl.strip(),
        description=(record.description or "").strip(),
        factor_type=record.factor_type.strip(),
        applicable_stock_pool=(record.applicable_stock_pool or "ALL").strip(),
        rebalance_cycle=(record.rebalance_cycle or "日线").strip(),
        factor_direction=record.factor_direction,
        source_url=(record.source_url or "").strip(),
    )


def generate_factor_md_from_text(
    *,
    user_text: str,
    forced_factor_id: Optional[str] = None,
    thinking_enabled: bool = True,
    temperature: float = 0.2,
    validate_dsl: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    """
    主入口：文本 → 标准因子 MD 字符串。

    返回：(md_content, llm_meta)
    """

    client = LlmGatewayClient()
    messages, user_obj = build_factor_extraction_messages(
        user_text=user_text,
        forced_factor_id=forced_factor_id,
    )

    thinking_type = "enabled" if thinking_enabled else "disabled"
    completion_text = client.chat_completions_create(
        messages=messages,
        temperature=temperature,
        thinking_type=thinking_type,
    )

    data = extract_first_json_object(completion_text)
    record = factor_record_from_llm_dict(data)

    dsl_text = record.formula_dsl.strip()
    if validate_dsl:
        validate_factor_dsl_formula(dsl_text)

    canonical = to_canonical(record, forced_factor_id=forced_factor_id)
    md = render_factor_md_content(canonical)

    llm_meta: Dict[str, Any] = {
        "user_payload": user_obj,
        "thinking_enabled": thinking_enabled,
        "temperature": temperature,
        "validate_dsl": validate_dsl,
        "model": client.gateway_cfg.model,
        "raw_completion_preview": completion_text[:2000],
        "canonical": asdict(canonical),
    }

    return md, llm_meta


def write_factor_md_file(
    *,
    md_content: str,
    output_path: Path,
    llm_meta: Optional[Dict[str, Any]] = None,
    write_sidecar_meta: bool = False,
) -> Path:
    """写入 `.md`；可选写入同名 `.meta.json` 便于审计。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md_content, encoding="utf-8")

    if write_sidecar_meta and llm_meta is not None:
        meta_path = output_path.with_suffix(output_path.suffix + ".meta.json")
        meta_path.write_text(json.dumps(llm_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("已写入因子文档: %s", output_path)
    return output_path
