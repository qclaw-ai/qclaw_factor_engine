#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
factors.directory 简易抓取适配器（MVP 占位版本）

当前实现只返回空列表，主要目的是把接口与调用链条搭好，后续再补真实抓取逻辑。
"""

from dataclasses import dataclass
from typing import List


@dataclass
class RawFactor:
    source: str
    name: str
    raw_formula: str
    description: str
    type: str
    universe: str
    period: str
    direction: str
    url: str


def fetch_factors(base_url: str, search_keywords: list[str]) -> List[RawFactor]:
    """
    从 factors.directory 抓取因子，返回 RawFactor 列表（MVP 先留空实现）。

    :param base_url: 站点基础 URL
    :param search_keywords: 搜索关键词列表
    """
    # TODO: 后续增加真实 HTTP 抓取逻辑：
    #  - 使用 requests/bs4 访问 factors.directory
    #  - 按关键字搜索因子
    #  - 解析名称 / 公式 / 描述等字段
    return []

