#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从自然语言 / 文本文件 / PDF /（可选）图片 生成标准因子 Markdown。

用法（在项目根目录）：

    python src/factor_md_generation/llm_md/cli.py --text "过去20日收益率"
    python -m factor_md_generation.llm_md.cli --text "过去20日收益率"

说明：直接运行本文件时脚本名为 __main__，不能使用相对导入；已将 `src` 加入 path 并改用绝对导入。

配置：`src/factor_md_generation/config_dev.ini`（非 prod）或 `config.ini`（prod）
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

# llm_md → factor_md_generation → src：保证 `python .../llm_md/cli.py` 与 `-m` 均可导入包
_SRC_ROOT = Path(__file__).resolve().parents[2]
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from common.utils import setup_logger

from factor_docs.factor_docs_parser import parse_factor_md_content
from factor_md_generation.io_paths import (
    DEFAULT_FACTOR_MD_CONFIG_FILE,
    resolve_llm_md_output_dir,
)
from factor_md_generation.llm_md.ingest import InputKind, normalize_user_input
from factor_md_generation.llm_md.llm_builder import generate_factor_md_from_text, write_factor_md_file
from factor_md_generation.llm_md.llm_client import LlmGatewayClient

logger = setup_logger("factor_md_from_input_cli", "logs/factor_md_from_input_cli.log")


def _default_output_md_path() -> Path:
    out_dir = resolve_llm_md_output_dir(config_file=DEFAULT_FACTOR_MD_CONFIG_FILE)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return out_dir / f"FACTOR_LLM_{ts}.md"


def _build_user_text(*, args: argparse.Namespace) -> str:
    if args.input:
        kind = InputKind(args.kind) if args.kind else None
        if kind == InputKind.IMAGE or (
            kind is None and Path(args.input).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}
        ):
            client = LlmGatewayClient()
            vision_text = client.describe_image_for_factor_extraction(
                image_path=args.input,
                user_hint=(args.text or "").strip(),
            )
            base = Path(args.input).name
            return f"[图片来源文件={base}]\n{vision_text}".strip()

        return normalize_user_input(text=args.text, file_path=args.input, kind=kind)

    if args.text_file:
        p = Path(args.text_file)
        body = p.read_text(encoding="utf-8")
        hint = (args.text or "").strip()
        if hint:
            return f"[用户补充说明]\n{hint}\n\n[文本文件={p.name}]\n{body}".strip()
        return body.strip()

    if args.text is None:
        raise ValueError("必须提供 --text、--text-file 或 --input 之一")

    return args.text.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="使用 LLM 生成标准因子 Markdown")
    parser.add_argument("--text", type=str, default=None, help="用户自然语言说明（可与文件组合）")
    parser.add_argument("--text-file", type=str, default=None, help="纯文本文件路径")
    parser.add_argument("--input", type=str, default=None, help="输入文件（pdf/文本/图片）")
    parser.add_argument(
        "--kind",
        type=str,
        choices=["text", "pdf", "image"],
        default=None,
        help="输入类型；留空则按扩展名推断（图片需 png/jpg 等后缀或显式 image）",
    )
    parser.add_argument("--factor-id", type=str, default=None, help="强制指定因子ID（写入 MD）")
    parser.add_argument("--output", type=str, default=None, help="输出 .md 路径；默认写入 factor_docs_dir")
    parser.add_argument("--no-thinking", action="store_true", help="关闭 thinking（若网关支持）")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--meta", action="store_true", help="额外写出 .md.meta.json 审计文件")
    parser.add_argument("--no-validate", action="store_true", help="跳过 parse_factor_md_content 校验")
    parser.add_argument(
        "--skip-dsl-check",
        action="store_true",
        help="跳过 formula_dsl 白名单静态校验（不推荐，仅排障）",
    )

    args = parser.parse_args()

    try:
        user_text = _build_user_text(args=args)
    except Exception as e:
        logger.error("%s", e)
        print(f"错误: {e}", file=sys.stderr)
        return 2

    if not user_text.strip():
        print("错误: 归一化后的用户文本为空", file=sys.stderr)
        return 2

    logger.info("归一化文本长度=%d", len(user_text))

    try:
        md, llm_meta = generate_factor_md_from_text(
            user_text=user_text,
            forced_factor_id=args.factor_id,
            thinking_enabled=not args.no_thinking,
            temperature=args.temperature,
            validate_dsl=not args.skip_dsl_check,
        )
    except Exception as e:
        logger.exception("LLM 生成失败")
        print(f"错误: {e}", file=sys.stderr)
        return 3

    if not args.no_validate:
        parsed = parse_factor_md_content(md, doc_path=args.output or "<stdout>")
        if parsed is None:
            print("错误: 生成结果未能通过 parse_factor_md_content 校验", file=sys.stderr)
            return 4

    out_path = Path(args.output) if args.output else _default_output_md_path()
    write_factor_md_file(
        md_content=md,
        output_path=out_path,
        llm_meta=llm_meta,
        write_sidecar_meta=args.meta,
    )

    if args.meta:
        print(json.dumps({"output_md": str(out_path.resolve())}, ensure_ascii=False))
    else:
        print(str(out_path.resolve()))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
