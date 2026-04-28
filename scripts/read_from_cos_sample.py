#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
公有读 COS 示例：按需下载 manifest/watermark 与 parquet，合并因子宽表与 label，供本地训练。

依赖：pandas 读 parquet 需要安装 pyarrow（见仓库 requirements.txt：pip install pyarrow）。

与仓库导出口径对齐：
- 主键：stock_code + trade_date（parquet 中 trade_date 为 YYYY-MM-DD 字符串）
- 因子：factor/universe=*/month=*/part-*.parquet
- label：stock_code, trade_date, y_ret_1d, y_ret_5d
- meta：
  meta/manifest/factor/{universe}/{month}.json
  meta/manifest/label/{universe}/{month}.json
  meta/watermark/factor/{universe}.json
  meta/watermark/label/{universe}.json
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import lightgbm as lgb
import pandas as pd
import requests


# =====================【按需修改的配置】=====================
# COS 公有读根 URL（末尾不要多余 /，代码里拼路径）
COS_PUBLIC_BASE = (
    "https://factor-data-1324221249.cos.ap-shanghai.myqcloud.com/factor_export_parquet"
)
# 本地缓存目录（自动创建）
LOCAL_CACHE_DIR = "./quant_factor_cache"
# ===========================================================


os.makedirs(LOCAL_CACHE_DIR, exist_ok=True)


class PublicFactorReader:
    """从公有读桶拉 meta + parquet；列名与本仓库导出一致。"""

    def __init__(self, universe: str = "ZZ500"):
        self.universe = universe
        self.remote_base = COS_PUBLIC_BASE.rstrip("/")
        self.local_base = LOCAL_CACHE_DIR

    def _download(self, remote_path: str) -> str:
        """远端相对路径 → 下载到本地 → 返回本地路径（已存在则跳过）。"""
        normalized = remote_path.replace("\\", "/").lstrip("/")
        local_path = os.path.join(self.local_base, *normalized.split("/"))
        os.makedirs(os.path.dirname(local_path), exist_ok=True)

        if os.path.exists(local_path):
            return local_path

        url = f"{self.remote_base}/{normalized}"
        print(f"下载: {url}")
        resp = requests.get(url, timeout=120)
        resp.raise_for_status()

        with open(local_path, "wb") as f:
            f.write(resp.content)

        return local_path

    @staticmethod
    def _load_json(local_path: str) -> Dict[str, Any]:
        with open(local_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_watermark(self, artifact: str = "factor") -> str:
        """
        读取某档产物的全域最新交易日 watermark（ISO 日期字符串）。
        artifact: factor | label
        """
        path = f"meta/watermark/{artifact}/{self.universe}.json"
        local = self._download(path)
        return str(self._load_json(local)["as_of_trade_date"]).strip()

    def get_training_end_date(self) -> str:
        """
        训练用的保守结束日：factor / label 两个 watermark 的较早者，
        避免一侧未更新导致 inner join 少一段。
        """
        wf = self.get_watermark("factor")
        wl = self.get_watermark("label")
        return wf if wf <= wl else wl

    def _iter_months(self, start_ym: str, end_ym: str) -> List[str]:
        """闭区间枚举 YYYY-MM。"""
        out: List[str] = []
        month = pd.to_datetime(start_ym + "-01")
        end = pd.to_datetime(end_ym + "-01")

        while month <= end:
            out.append(month.strftime("%Y-%m"))
            month = month + pd.DateOffset(months=1)

        return out

    def load_manifest_parquet_concat(
        self,
        *,
        artifact: str,
        start_date: str,
        end_date: str,
        skip_missing_month: bool = True,
    ) -> pd.DataFrame:
        """
        按月在 manifest/part_rel_paths 下载 parquet，纵向合并后按交易日过滤。

        :param artifact: factor | label
        """
        start_ym = start_date[:7]
        end_ym = end_date[:7]

        dfs: List[pd.DataFrame] = []
        manifest_prefix = f"meta/manifest/{artifact}/{self.universe}/"

        # 新版本：meta/manifest/{factor|label}/{universe}/{month}.json
        # 兼容 factor 旧路径：meta/manifest/{universe}/{month}.json
        for ym in self._iter_months(start_ym, end_ym):
            mp = f"{manifest_prefix}{ym}.json"
            local_path: Optional[str] = None

            try:
                local_path = self._download(mp)
            except Exception as e:
                if artifact == "factor":
                    legacy_mp = f"meta/manifest/{self.universe}/{ym}.json"
                    try:
                        local_path = self._download(legacy_mp)
                    except Exception:
                        if skip_missing_month:
                            print(
                                f"[WARN] 跳过月份（factor 无 manifest）: {ym} err={e}",
                            )

                            continue

                        raise

                else:
                    if skip_missing_month:
                        print(f"[WARN] 跳过月份（label 无 manifest）: {ym} err={e}")

                        continue

                    raise

            manifest = self._load_json(local_path)

            for rel in manifest.get("part_rel_paths") or []:
                pcl = self._download(rel.replace("\\", "/"))
                dfs.append(pd.read_parquet(pcl))

        if not dfs:
            return pd.DataFrame()

        df = pd.concat(dfs, ignore_index=True)

        # 与导出对齐：trade_date（若历史混用 date 则统一）
        if "date" in df.columns and "trade_date" not in df.columns:
            df = df.rename(columns={"date": "trade_date"})
        elif "trade_date" not in df.columns:
            raise ValueError(f"{artifact}: 缺少 trade_date / date 列，实际列={list(df.columns)[:20]}")

        key_cols = ["stock_code", "trade_date"]
        df = df.drop_duplicates(subset=key_cols, keep="last")

        df["trade_date"] = df["trade_date"].astype(str).str.slice(0, 10)

        df = df[(df["trade_date"] >= start_date) & (df["trade_date"] <= end_date)]

        return df

    def get_train_data(
        self,
        start_date: str = "2016-01-01",
        end_date: Optional[str] = None,
        *,
        skip_missing_month: bool = True,
    ) -> pd.DataFrame:
        """合并因子宽表与 label（inner join）；结束日默认取双 watermark 保守值。"""
        end_date = end_date or self.get_training_end_date()

        print(f"加载因子 {start_date} ~ {end_date}")

        factor = self.load_manifest_parquet_concat(
            artifact="factor",
            start_date=start_date,
            end_date=end_date,
            skip_missing_month=skip_missing_month,
        )

        print(f"加载 label {start_date} ~ {end_date}")

        label = self.load_manifest_parquet_concat(
            artifact="label",
            start_date=start_date,
            end_date=end_date,
            skip_missing_month=skip_missing_month,
        )

        if factor.empty:
            raise ValueError(
                "factor 为空：请确认 COS 路径、月份与 universe 是否与导出一致。"
            )

        if label.empty:
            raise ValueError(
                "label 为空：请确认已导出 label/meta，且日期区间有重叠。"
            )

        train = pd.merge(factor, label, on=["stock_code", "trade_date"], how="inner")

        print(f"训练集就绪：{len(train)} 行，列数={train.shape[1]}")

        return train


LABEL_COLS = {"y_ret_1d", "y_ret_5d"}

# ===================== 客户一键示例训练 =====================
if __name__ == "__main__":
    reader = PublicFactorReader(universe="ZZ500")

    train_df = reader.get_train_data(
        start_date="2025-08-01",
        end_date="2025-09-30",
    )

    exclude = {"stock_code", "trade_date", *LABEL_COLS}
    feature_cols = [c for c in train_df.columns if c not in exclude]

    if not feature_cols:
        raise RuntimeError(
            "无任何特征列，请确认 factor parquet 已为宽表因子列。"
        )

    target = "y_ret_1d"
    if target not in train_df.columns:
        present_labels = [c for c in train_df.columns if c in LABEL_COLS]
        raise KeyError(f"标签列缺失: 需要 {target}，标签相关列={present_labels}")

    X = train_df[feature_cols]

    y = train_df[target]

    model = lgb.train(
        params={
            "objective": "regression",
            "metric": "mse",
            "learning_rate": 0.05,
            "verbosity": 1,
        },
        train_set=lgb.Dataset(X, label=y),
        num_boost_round=200,
    )

    model.save_model("lgb_factor_model.txt")

    print(f"训练完成（目标={target}）；模型已保存 lgb_factor_model.txt")
