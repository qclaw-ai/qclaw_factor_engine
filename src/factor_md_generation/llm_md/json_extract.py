#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从 LLM 输出中提取 JSON 对象（与 strategy_config_builder 思路对齐）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict


def strip_code_fences(text: str) -> str:
    """兼容 ```json ... ``` / ``` ... ``` 等围栏。"""

    text = re.sub(r"```json", "```", text, flags=re.IGNORECASE)
    return text.replace("```", "")


def extract_first_json_object(text: str) -> Dict[str, Any]:
    """
    从模型输出中提取第一个合法 JSON 对象（dict）。

    说明：
    - thinking=enabled 时，模型更可能输出混杂文本；
    - 因此解析不依赖 JSON 必须从开头开始。
    """

    cleaned = strip_code_fences(text)

    start_index: int = -1
    stack: int = 0

    for i, ch in enumerate(cleaned):
        if ch == "{":
            if stack == 0:
                start_index = i
            stack += 1
        elif ch == "}":
            if stack > 0:
                stack -= 1
                if stack == 0 and start_index >= 0:
                    candidate = cleaned[start_index : i + 1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            return data
                    except Exception:
                        start_index = -1

    raise ValueError("未能从模型输出中提取到合法 JSON 对象（dict）")
