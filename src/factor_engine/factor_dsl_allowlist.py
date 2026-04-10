#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
因子 DSL（`factor_engine_runner.compute_factor_values` 的 eval locals）白名单。

重要：
- 必须与 `compute_factor_values` 内 `locals_dict` 的键集合保持完全一致；
- 修改 `locals_dict` 时请同步更新本文件，否则运行时会触发断言失败。
"""

from __future__ import annotations

import ast
from typing import FrozenSet, Iterable, Optional, Set


# 行情/中间量等：在 eval 环境中注入为 pandas.Series（或等价序列）
DSL_SERIES_NAMES: FrozenSet[str] = frozenset(
    {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
        "VWAP",
        "vwap",
        "RET",
        "ret",
        "MKT",
        "SMB",
        "HML",
        "BANCHMARKINDEXCLOSE",
        "BANCHMARKINDEXOPEN",
        "DTM",
        "DBM",
        "TR",
        "HD",
        "LD",
        "dtm",
        "dbm",
        "tr",
        "hd",
        "ld",
        "banchmarkindexclose",
        "banchmarkindexopen",
    }
)


# 可调用算子：在 eval 环境中注入为函数
DSL_CALLABLE_NAMES: FrozenSet[str] = frozenset(
    {
        "SEQUENCE",
        "sequence",
        "MA",
        "REF",
        "LOG",
        "DELTA",
        "RANK",
        "CORR",
        "TS_SUM",
        "TS_MAX",
        "TS_MIN",
        "TS_RANK",
        "STD",
        "STDDEV",
        "SUM",
        "ABS",
        "SIGN",
        "MIN",
        "MAX",
        "POW",
        "POWER",
        "SCALE",
        "IF",
        "COVIANCE",
        "COVARIANCE",
        "SMEAN",
        "PROD",
        "COUNT",
        "REGBETA",
        "REGRESI",
        "SUMIF",
        "WMA",
        "DECAYLINEAR",
        "FILTER",
        "HIGHDAY",
        "LOWDAY",
        "SUMAC",
        "log",
        "delta",
        "rank",
        "corr",
        "ts_sum",
        "ts_max",
        "ts_min",
        "ts_rank",
        "std",
        "stddev",
        "sum",
        "abs",
        "sign",
        "min",
        "max",
        "pow",
        "power",
        "scale",
        "if",
        "covariance",
        "smean",
        "prod",
        "count",
        "regbeta",
        "regresi",
        "sumif",
        "wma",
        "decaylinear",
        "filter",
        "highday",
        "lowday",
        "sumac",
    }
)


DSL_EVAL_LOCAL_KEYS: FrozenSet[str] = DSL_SERIES_NAMES | DSL_CALLABLE_NAMES


# Python 字面量（极少数公式可能出现；不作为“行情字段”白名单的一部分）
_DSL_EXTRA_NAMES: FrozenSet[str] = frozenset({"True", "False", "None"})


def assert_locals_dict_keys_match_allowlist(keys: Iterable[str]) -> None:
    """运行时校验：compute_factor_values 注入的 locals 键集与本模块定义一致。"""

    actual: Set[str] = set(keys)
    expected = DSL_EVAL_LOCAL_KEYS
    if actual == expected:
        return

    only_in_actual = sorted(actual - expected)
    only_in_expected = sorted(expected - actual)
    raise AssertionError(
        "compute_factor_values.locals_dict 键集与 factor_dsl_allowlist 不一致："
        f"only_in_locals={only_in_actual} only_in_allowlist={only_in_expected}"
    )


def validate_factor_dsl_formula(
    formula: str,
    *,
    allowed_names: Optional[FrozenSet[str]] = None,
) -> None:
    """
    静态校验：formula 作为 Python 表达式解析，所有 `ast.Name` 必须落在白名单（或 True/False/None）。

    说明：
    - 不保证语义正确，仅拦截明显非法标识符（如 np、EMA 等）；
    - 语法错误会抛出 ValueError。
    """

    text = (formula or "").strip()
    if not text:
        raise ValueError("formula_dsl 为空")

    allowed = allowed_names or DSL_EVAL_LOCAL_KEYS

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"formula_dsl 不是合法 Python 表达式: {e}") from e

    bad: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if node.id in _DSL_EXTRA_NAMES:
                continue
            if node.id in allowed:
                continue
            bad.add(node.id)

    if bad:
        unknown = ", ".join(sorted(bad))
        raise ValueError(
            "formula_dsl 含未在白名单中的标识符: "
            f"{unknown}。"
            "请仅使用 factor_engine.factor_dsl_allowlist 中列出的字段与函数。"
        )


def build_llm_dsl_constraints_text() -> str:
    """供 LLM system prompt 使用的短说明（完整列表放在 user JSON 更省 token 混乱）。"""

    return (
        "公式(DSL) 必须是单个 Python 表达式，可直接被引擎用受限 eval 求值。"
        "除数值与运算符外，标识符只能来自我随后在 user JSON 里给出的 "
        "`dsl_allowed_series` 与 `dsl_allowed_functions`。"
        "不要写 np. / pd. 等命名空间，不要发明未列出的函数名。"
        "行情字段优先使用小写 open/high/low/close/volume/turnover；"
        "算子可用大写（如 CORR、RANK）或小写别名（如 corr、rank），与列表一致即可。"
    )


def build_llm_dsl_allowlist_payload() -> dict:
    """写入 LLM user JSON，约束 formula_dsl。"""

    return {
        "dsl_allowed_series": sorted(DSL_SERIES_NAMES),
        "dsl_allowed_functions": sorted(DSL_CALLABLE_NAMES),
        "dsl_expression_rule": (
            "formula_dsl 为单表达式；允许 + - * / 与括号；"
            "禁止 import/语句/列表推导；不要添加未出现在两个 allowed 列表中的标识符。"
        ),
    }
