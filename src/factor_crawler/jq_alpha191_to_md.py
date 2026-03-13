#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
从 JoinQuant alpha191 HTML 文档（alpha.html）中解析因子定义，
生成符合项目规范的因子 md 文件。

注意：
- 本脚本当前版本只做「结构解析 + 原始公式搬运」：
  - 公式原文: 保留 JoinQuant 的 alpha191 DSL 表达；
  - 公式(DSL): 先直接等于原文，后续再按需映射到本项目内部 DSL。
- 运行前请确保已安装 beautifulsoup4:
  pip install beautifulsoup4
"""

import os
import re
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import List

from bs4 import BeautifulSoup  # type: ignore


@dataclass
class RawFactor:
    """JoinQuant alpha191 原始因子定义（抓取层结构）"""

    source: str
    alpha_name: str       # 如 alpha_001
    factor_id: str        # 内部因子ID，如 JQ_ALPHA_001
    raw_formula: str      # JoinQuant alpha191 原始公式字符串
    description: str      # 简要描述（当前版本可为空）
    url: str              # 来源 URL（当前版本统一为 alpha191 文档地址）


def _project_root() -> Path:
    """推断项目根目录（qclaw_factor_engine）"""
    # 当前文件: src/factor_crawler/jq_alpha191_to_md.py
    return Path(__file__).resolve().parents[2]


def _get_paths() -> tuple[Path, Path]:
    """返回 alpha.html 路径与输出 md 目录"""
    root = _project_root()
    html_path = root / "src" / "factor_crawler" / "JoinQuant" / "alpha.html"
    output_dir = root / "factor_docs" / "md" / "JoinQuant_alpha191"
    return html_path, output_dir


def _normalize_factor_id(alpha_name: str) -> str:
    """
    alpha_001 -> JQ_ALPHA_001
    alpha001  -> JQ_ALPHA_001
    """
    m = re.search(r"(\d+)", alpha_name)
    num = m.group(1).zfill(3) if m else "000"
    return f"JQ_ALPHA_{num}"


def _extract_alpha_blocks(html_path: Path) -> List[RawFactor]:
    """从 alpha.html 中解析出所有 alpha 因子定义"""
    if not html_path.is_file():
        raise FileNotFoundError(f"JoinQuant alpha191 HTML 文件不存在: {html_path}")

    with html_path.open("r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "html.parser")

    # 主体内容区域
    preview = soup.find(id="preview-contents") or soup

    factors: List[RawFactor] = []

    for h4 in preview.find_all("h4"):
        h_id = h4.get("id", "") or ""
        if not h_id.startswith("alpha"):
            continue

        # 1) 解析 alpha 函数名：alpha_001 / alpha001 等
        title_text = h4.get_text(strip=True)
        m = re.search(r"(alpha[_]?\d+)", title_text, flags=re.IGNORECASE)
        if m:
            alpha_name = m.group(1)
        else:
            # 退化：从 id="alpha001" 推导
            num = re.sub(r"[^\d]", "", h_id)
            alpha_name = f"alpha_{num.zfill(3)}"

        factor_id = _normalize_factor_id(alpha_name)

        # 2) 向后查找「因子公式」对应的 li
        raw_formula: str | None = None
        description: str = ""

        node = h4
        while node is not None and raw_formula is None:
            node = node.find_next_sibling()
            if node is None:
                break

            # 优先处理 <ul> 结构
            if node.name == "ul":
                lis = node.find_all("li")
                for li in lis:
                    text = li.get_text(" ", strip=True)
                    # 公式
                    if "因子公式" in text:
                        sub_ul = li.find("ul")
                        if sub_ul:
                            sub_li = sub_ul.find("li")
                            if sub_li:
                                raw = sub_li.get_text(" ", strip=True)
                                raw_formula = raw
                                break
                        else:
                            # 因子公式直接写在当前 li
                            raw = li.get_text(" ", strip=True)
                            raw = re.sub(r"^因子公式[:：]\s*", "", raw)
                            raw_formula = raw
                            break

                    # 描述（如果有「说明」/「描述」关键字可以顺带抓一下）
                    if not description and ("说明" in text or "描述" in text):
                        description = text

        if not raw_formula:
            # 找不到公式的因子直接跳过，避免生成无用 md
            continue

        raw_formula = unescape(raw_formula).strip()

        factors.append(
            RawFactor(
                source="JoinQuant_alpha191",
                alpha_name=alpha_name,
                factor_id=factor_id,
                raw_formula=raw_formula,
                description=description,
                url="https://www.joinquant.com/view/community/detail/alpha191",  # 统一文档链接
            )
        )

    return factors


def _to_internal_dsl(raw: RawFactor) -> str:
    """
    将 JoinQuant alpha191 的公式映射到项目内部 DSL。

    当前版本 V0：直接返回原始公式字符串，仅做占位。
    后续可以在这里逐步替换为:
      - 字段名映射: OPEN/HIGH/LOW/CLOSE/VOLUME/AMOUNT -> open/high/...
      - 函数名映射: DELAY -> REF, MEAN -> MA, 等等。
    """
    formula = raw.raw_formula

    # 1) 粗清洗：全角标点 / OCR 错误等
    formula = formula.replace("，", ",")
    formula = formula.replace("DELA Y", "DELAY")
    formula = formula.replace("HI GH", "HIGH")
    formula = formula.replace("CL OSE", "CLOSE")
    # HTML 转义残留：RET\<0 之类，统一还原成 RET<0
    formula = formula.replace(r"RET\<0", "RET<0")
    formula = formula.replace(r"\<", "<")
    # 全角问号 -> 半角，避免 ？ 出现在三目表达式里
    formula = formula.replace("？", "?")

    # 2) 字段归一化：统一成 engine 里用的小写字段名
    field_replacements = {
        "OPEN": "open",
        "HIGH": "high",
        "LOW": "low",
        "CLOSE": "close",
        "VOLUME": "volume",
        "AMOUNT": "turnover",
        "TURNOVER": "turnover",
        "VWAP": "vwap",
    }

    # 3) 函数名归一化到引擎里的“规范名”（引擎同时也兼容大小写）
    func_replacements = {
        # alpha191 / 其他来源 -> 内部 DSL 规范名
        "DELAY": "REF",
        "MEAN": "MA",
        "SMA": "MA",          # 先粗暴映射成简单 MA，后面有需要再细化
        "SMEAN": "MA",
        "STDDEV": "STD",
        "TSRANK": "TS_RANK",
        "TSMIN": "TS_MIN",
        "TSMAX": "TS_MAX",
        "YSRANK": "TS_RANK",
    }

    def _replace_word(s: str, mapping: dict[str, str]) -> str:
        for k, v in mapping.items():
            # \b 确保是完整 token，避免替到字段名内部
            s = re.sub(rf"\b{k}\b", v, s)
        return s

    formula = _replace_word(formula, field_replacements)
    formula = _replace_word(formula, func_replacements)

    # 替换 alpha191 的幂运算符 ^ 为 Python 的 **
    formula = formula.replace("^", "**")
    # JQ 文档里的 ./ 和 .* 对应 element-wise 运算，DSL 里用普通 / 和 *
    formula = formula.replace("./", "/").replace(".*", "*")

    # 3) 逻辑运算符和比较运算符规范化
    #   - alpha191: && / || / =
    #   - Python + pandas: & / | / ==
    formula = formula.replace("&&", "&").replace("||", "|")

    # 把“单独的 =”替换成“==”，避免影响 >= <= == 等
    formula = re.sub(r"(?<![<>=!])=(?!=)", "==", formula)

    # 4) 三目运算 A?B:C 转为 IF(A, B, C)
    #    采用「按括号深度扫描」策略，每次处理最左侧的 ?，直到没有 ? 为止。

    def _convert_ternary(expr: str) -> str:
        """递归把三目表达式 A?B:C 转换为 IF(A, B, C)

        策略：
        - 每次找到最左侧的 '?'；
        - 向左按括号深度扫描找 condition 的起始边界
          （遇到「未匹配的 (」或「同层 ,」或串首 → 停）；
        - 向右找匹配的 ':'（跳过嵌套 ?: 对）；
        - 再向右按括号深度扫描找 false_part 的结束边界
          （遇到「未匹配的 )」或「同层 ,」或串尾 → 停）；
        - 替换成 IF(cond, true_part, false_part)，从头重新扫描。
        """

        while "?" in expr:
            q = expr.find("?")
            if q == -1:
                break

            # ---- 1) 向左找 condition 的起始位置 ----
            # 按括号深度扫描：遇到 ) 深度 +1，遇到 ( 深度 -1；
            # 当深度变为负数（未匹配的 (）、遇到同层逗号、或到达串首时停止。
            depth = 0
            cond_start = 0
            j = q - 1
            while j >= 0:
                ch = expr[j]
                if ch == ")":
                    depth += 1
                elif ch == "(":
                    if depth == 0:
                        # 未匹配的 (，condition 从它后面开始
                        cond_start = j + 1
                        break
                    depth -= 1
                elif ch == "," and depth == 0:
                    # 同层逗号，condition 从它后面开始
                    cond_start = j + 1
                    break
                j -= 1

            if j < 0:
                # 扫到了字符串最左边
                cond_start = 0

            condition = expr[cond_start:q].strip()

            # ---- 2) 向右找匹配的 :（跳过嵌套的 ?: 对）----
            nested = 0
            colon = q + 1
            while colon < len(expr):
                if expr[colon] == "?":
                    nested += 1
                elif expr[colon] == ":":
                    if nested == 0:
                        break
                    nested -= 1
                colon += 1

            if colon >= len(expr) or expr[colon] != ":":
                # 找不到配对的 :，异常情况，跳过
                break

            true_part = expr[q + 1:colon].strip()

            # ---- 3) 向右找 false_part 的结束位置 ----
            # 同样按括号深度扫描：遇到未匹配的 ) 或同层 , 或串尾时停止。
            depth = 0
            k = colon + 1
            while k < len(expr):
                ch = expr[k]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    if depth == 0:
                        # 未匹配的 )，false_part 到这里结束
                        break
                    depth -= 1
                elif ch == "," and depth == 0:
                    break
                k += 1

            false_part = expr[colon + 1:k].strip()

            before = expr[:cond_start]
            after = expr[k:]
            expr = before + f"IF({condition},{true_part},{false_part})" + after

        return expr

    formula = _convert_ternary(formula)

    # 简单配平括号：部分 HTML 片段在抓取时可能丢失结尾的 ')'
    # 这里按字符级别统计 '(' 和 ')'，若 '(' 更多，则在末尾补齐缺失 ')'
    left = formula.count("(")
    right = formula.count(")")
    if left > right:
        formula = formula + (")" * (left - right))

    return formula


def _write_md(output_dir: Path, raw: RawFactor, internal_formula: str) -> None:
    """根据 RawFactor + 内部 DSL 公式生成 md 文件"""
    output_dir.mkdir(parents=True, exist_ok=True)

    file_name = f"{raw.factor_id}.md"
    path = output_dir / file_name

    # 统一使用项目中的 md 模板风格
    lines = [
        "# JoinQuant Alpha191 因子（自动导入）",
        "",
        f"因子ID: {raw.factor_id}  ",
        f"因子名称: {raw.alpha_name}  ",
        f"公式原文: {raw.raw_formula}  ",
        f"公式(DSL): {internal_formula}  ",
        f"描述: {raw.description or '自动从 JoinQuant Alpha191 文档导入的因子公式，尚未对口径和实现做人工校验。'}  ",
        "因子类型: 量价  ",
        "适用股票池: ALL_A  ",
        "调仓周期: 日线  ",
        "因子方向: long  ",
        f"来源URL: {raw.url}",
        "",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    html_path, output_dir = _get_paths()
    print(f"读取 JoinQuant alpha191 文件: {html_path}")

    factors = _extract_alpha_blocks(html_path)
    print(f"解析到 {len(factors)} 个因子定义，将生成 md 文件到: {output_dir}")

    for raw in factors:
        internal = _to_internal_dsl(raw)
        _write_md(output_dir, raw, internal)

    print("完成 JoinQuant alpha191 -> md 生成。")


if __name__ == "__main__":
    main()

