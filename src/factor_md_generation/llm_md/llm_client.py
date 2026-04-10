#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 网关客户端（OpenAI 兼容 Chat Completions）。

配置来自包外层：`src/factor_md_generation/config*.ini`。

apiKey 读取顺序：
1) 环境变量 `LKEAP_API_KEY`（非空则优先）
2) 否则读取 ini 中 `[llm_gateway] apiKey`
   - 生产：`ENV=prod` → `config.ini`
   - 非生产：`config_dev.ini`
"""

from __future__ import annotations

import base64
import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI

sys.path.append(os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import Config

from factor_md_generation.io_paths import DEFAULT_FACTOR_MD_CONFIG_FILE


@dataclass
class LLMGatewayConfig:
    """LLM 网关配置（OpenAI 兼容层）。"""

    base_url: str
    api_key: str
    model: str
    thinking_type: str = "disabled"
    timeout_sec: int = 120
    max_tokens: int = 8192
    temperature: float = 0.2
    vision_model: str = ""


def _load_llm_gateway_config() -> LLMGatewayConfig:
    cfg = Config(config_file=DEFAULT_FACTOR_MD_CONFIG_FILE)

    try:
        base_url = cfg.get("llm_gateway", "baseUrl", fallback="https://api.lkeap.cloud.tencent.com/v1").strip()
    except Exception:
        base_url = "https://api.lkeap.cloud.tencent.com/v1"

    # 环境变量优先；未设置时走 ini
    api_key = os.getenv("LKEAP_API_KEY", "").strip()
    if not api_key:
        try:
            api_key = cfg.get("llm_gateway", "apiKey", fallback="").strip()
        except Exception:
            api_key = ""

    if not api_key or "PUT_YOUR_LKEAP_API_KEY_HERE" in api_key or "sk-xxx" in api_key:
        raise RuntimeError(
            "LLM 网关 API Key 缺失或仍为占位符："
            "请设置环境变量 `LKEAP_API_KEY`，"
            "或在 `src/factor_md_generation/config.ini`（ENV=prod）"
            " / `config_dev.ini` 的 [llm_gateway] 填写 apiKey。"
        )

    try:
        model = cfg.get("llm_gateway", "model", fallback="deepseek-v3.2").strip()
    except Exception:
        model = "deepseek-v3.2"

    try:
        thinking_type = cfg.get("llm_gateway", "thinking_type", fallback="enabled").strip()
    except Exception:
        thinking_type = "enabled"

    timeout_sec = cfg.getint("llm_gateway", "timeout_sec", fallback=120)
    max_tokens = cfg.getint("llm_gateway", "max_tokens", fallback=8192)
    temperature = cfg.getfloat("llm_gateway", "temperature", fallback=0.2)

    try:
        vision_model = cfg.get("llm_gateway", "vision_model", fallback="").strip()
    except Exception:
        vision_model = ""

    return LLMGatewayConfig(
        base_url=base_url,
        api_key=api_key,
        model=model,
        thinking_type=thinking_type,
        timeout_sec=timeout_sec,
        max_tokens=max_tokens,
        temperature=temperature,
        vision_model=vision_model,
    )


class LlmGatewayClient:
    """
    非流式 Chat Completions；可选 thinking.type（与现有策略 builder 对齐）。
    """

    def __init__(self) -> None:
        self.gateway_cfg = _load_llm_gateway_config()
        self._client = OpenAI(
            api_key=self.gateway_cfg.api_key,
            base_url=self.gateway_cfg.base_url,
            timeout=self.gateway_cfg.timeout_sec,
        )

    def chat_completions_create(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        thinking_type: Optional[str] = None,
    ) -> str:
        """返回 assistant 的文本内容（非流式）。"""

        use_model = model or self.gateway_cfg.model
        use_temperature = temperature if temperature is not None else self.gateway_cfg.temperature
        use_max_tokens = max_tokens if max_tokens is not None else self.gateway_cfg.max_tokens
        use_thinking_type = thinking_type if thinking_type is not None else self.gateway_cfg.thinking_type

        extra_body: Dict[str, Any] = {}
        if use_thinking_type:
            extra_body = {"thinking": {"type": use_thinking_type}}

        completion = self._client.chat.completions.create(
            model=use_model,
            messages=messages,
            temperature=use_temperature,
            max_tokens=use_max_tokens,
            extra_body=extra_body if extra_body else None,
        )

        return completion.choices[0].message.content or ""

    def describe_image_for_factor_extraction(
        self,
        *,
        image_path: str,
        user_hint: str = "",
    ) -> str:
        """
        将图片转为「可供因子抽取使用的中文文本描述」。

        使用 [llm_gateway] vision_model；未配置则抛错，由上层决定是否降级。
        """

        if not self.gateway_cfg.vision_model:
            raise RuntimeError(
                "未配置 vision_model：请在 `src/factor_md_generation/config_dev.ini` 的 [llm_gateway] 中设置 vision_model，"
                "或改用文本/PDF 输入。"
            )

        with open(image_path, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode("ascii")

        ext = os.path.splitext(image_path)[1].lower().lstrip(".") or "png"
        if ext == "jpg":
            ext = "jpeg"

        data_url = f"data:image/{ext};base64,{b64}"

        system = (
            "你是量化研究助手。请阅读图片，把与「因子定义/公式/指标」相关的信息整理成纯文本，"
            "保留数学表达式与符号，不要编造看不清的内容。"
        )

        user_parts: List[Dict[str, Any]] = [
            {"type": "text", "text": (user_hint or "请根据图片提取与因子相关的文字与公式要点。").strip()},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_parts},
        ]

        # 视觉模型通常不支持 thinking 扩展：这里强制关闭 extra_body
        completion = self._client.chat.completions.create(
            model=self.gateway_cfg.vision_model,
            messages=messages,
            temperature=self.gateway_cfg.temperature,
            max_tokens=self.gateway_cfg.max_tokens,
        )

        return completion.choices[0].message.content or ""
