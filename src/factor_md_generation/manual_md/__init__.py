# -*- coding: utf-8 -*-
"""
手写 / 表单提交 → 标准因子 Markdown 落盘（供 FastAPI 等调用）。

渲染与 LLM 路径共用 `llm_md.canonical`；配置与输出目录共用包外层 `config*.ini`。
"""

from factor_md_generation.manual_md.service import save_manual_factor_md

__all__ = ["save_manual_factor_md"]
