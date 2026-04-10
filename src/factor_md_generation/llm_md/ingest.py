#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将网页/CLI 多源输入统一为「纯文本」，供 LLM 因子抽取使用。

- 文本：按编码读取或直接使用字符串
- PDF：依赖 pypdf
- 图片：由 `llm_client.LlmGatewayClient.describe_image_for_factor_extraction` 先变成文本
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional, Union


class InputKind(str, Enum):
    TEXT = "text"
    PDF = "pdf"
    IMAGE = "image"


def read_text_file(path: Union[str, Path], *, encoding: str = "utf-8") -> str:
    """读取文本文件（UTF-8 优先）。"""

    p = Path(path)
    with p.open("r", encoding=encoding) as f:
        return f.read()


def extract_text_from_pdf(path: Union[str, Path]) -> str:
    """从 PDF 抽取纯文本（适合文本型 PDF；扫描版效果差）。"""

    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ImportError("读取 PDF 需要安装 pypdf：pip install pypdf") from e

    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        t = t.strip()
        if t:
            parts.append(t)

    return "\n\n".join(parts).strip()


def normalize_user_input(
    *,
    text: Optional[str] = None,
    file_path: Optional[Union[str, Path]] = None,
    kind: Optional[Union[str, InputKind]] = None,
) -> str:
    """
    将用户输入统一为一段 UTF-8 文本。

    - 若提供 `text` 且未给 `file_path`，直接返回 strip 后的 text。
    - 若提供 `file_path`：
      - kind=text 或未识别扩展名：按文本文件读
      - kind=pdf 或 .pdf：走 PDF 抽取
      - kind=image 或常见图片后缀：需先走视觉模型，此处会抛错提示
    """

    if text is not None and not str(text).strip() and file_path is None:
        raise ValueError("text 与 file_path 不能同时为空")

    if file_path is None:
        return (text or "").strip()

    p = Path(file_path)
    if not p.is_file():
        raise FileNotFoundError(f"文件不存在: {p}")

    suffix = p.suffix.lower()
    kind_norm: Optional[InputKind] = None
    if isinstance(kind, InputKind):
        kind_norm = kind
    elif isinstance(kind, str) and kind.strip():
        kind_norm = InputKind(kind.strip().lower())

    if kind_norm == InputKind.PDF or suffix == ".pdf":
        body = extract_text_from_pdf(p)
        hint = (text or "").strip()
        if hint:
            return f"[用户补充说明]\n{hint}\n\n[PDF 抽取正文]\n{body}".strip()
        return body

    if kind_norm == InputKind.IMAGE or suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        raise ValueError(
            "图片输入请先调用 `factor_md_generation.llm_md.llm_client.LlmGatewayClient."
            "describe_image_for_factor_extraction`，再把返回文本作为 text 传入流水线。"
        )

    body = read_text_file(p)
    hint = (text or "").strip()
    if hint:
        return f"[用户补充说明]\n{hint}\n\n[文件内容]\n{body}".strip()
    return body.strip()
