#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
每日增量同步：只上传“本月”相关的数据与 meta（factor + label）。

为什么需要 daily 版本：
- 你们导出是“按月目录 + part-*.parquet + manifest/watermark”，每天只会更新：
  - factor/universe=U/month=YYYY-MM/part-*.parquet
  - label/universe=U/month=YYYY-MM/part-*.parquet
  - meta/manifest/factor/U/YYYY-MM.json
  - meta/manifest/label/U/YYYY-MM.json
  - meta/watermark/factor/U.json
  - meta/watermark/label/U.json
- 因此 daily 同步可以避免每次全量 walk + 上传，成本更低、更快。
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from qcloud_cos import CosConfig, CosS3Client


def _normalize_cos_key(cos_root: str, rel_path: str) -> str:
    left = (cos_root or "").strip().strip("/")
    right = (rel_path or "").strip().replace("\\", "/").lstrip("/")

    if not left:
        return right

    if not right:
        return left

    return f"{left}/{right}"


def _build_client(
    *,
    secret_id: str,
    secret_key: str,
    region: str,
    endpoint: str,
) -> CosS3Client:
    config = CosConfig(
        Region=region,
        SecretId=secret_id,
        SecretKey=secret_key,
        Endpoint=endpoint,
    )
    return CosS3Client(config)


def _upload_one(
    *,
    client: CosS3Client,
    bucket: str,
    local_file_path: Path,
    cos_key: str,
) -> Tuple[str, int]:
    client.upload_file(
        Bucket=bucket,
        LocalFilePath=str(local_file_path),
        Key=cos_key,
    )
    return cos_key, local_file_path.stat().st_size


def _collect_month_paths(
    *,
    local_root: Path,
    universe: str,
    month: str,
) -> List[Path]:
    """
    收集 daily 同步所需的本地文件路径列表。

    约定：local_root = artifacts/factor_export_parquet（导出根）
    """
    paths: List[Path] = []

    # factor parts
    factor_dir = local_root / "factor" / f"universe={universe}" / f"month={month}"
    if factor_dir.exists():
        paths.extend(sorted(factor_dir.glob("part-*.parquet")))

    # label parts
    label_dir = local_root / "label" / f"universe={universe}" / f"month={month}"
    if label_dir.exists():
        paths.extend(sorted(label_dir.glob("part-*.parquet")))

    # meta manifests（按月）
    paths.append(local_root / "meta" / "manifest" / "factor" / universe / f"{month}.json")
    paths.append(local_root / "meta" / "manifest" / "label" / universe / f"{month}.json")

    # meta watermarks（按域）
    paths.append(local_root / "meta" / "watermark" / "factor" / f"{universe}.json")
    paths.append(local_root / "meta" / "watermark" / "label" / f"{universe}.json")

    # 去重 + 过滤不存在
    uniq = []
    seen = set()
    for p in paths:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        if p.exists() and p.is_file():
            uniq.append(p)

    return uniq


def run_upload_daily(
    *,
    local_root: Path,
    cos_root: str,
    bucket: str,
    secret_id: str,
    secret_key: str,
    region: str,
    endpoint: str,
    universe: str,
    month: str,
    workers: int,
    strict: bool,
) -> None:
    local_root = local_root.resolve()
    if not local_root.exists():
        raise FileNotFoundError(f"local_root 不存在: {local_root}")

    client = _build_client(
        secret_id=secret_id,
        secret_key=secret_key,
        region=region,
        endpoint=endpoint,
    )

    files = _collect_month_paths(local_root=local_root, universe=universe, month=month)
    if not files:
        msg = f"未发现任何可上传文件 universe={universe} month={month} local_root={local_root}"
        if strict:
            raise SystemExit(msg)
        print(f"[SKIP] {msg}")
        return

    print(f"[INFO] upload_daily local_root={local_root} files={len(files)} bucket={bucket} cos_root={cos_root} endpoint={endpoint}")

    total_bytes = 0
    ok = 0
    fail = 0

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        futures = []

        for p in files:
            rel = p.relative_to(local_root).as_posix()
            key = _normalize_cos_key(cos_root, rel)
            futures.append(
                ex.submit(
                    _upload_one,
                    client=client,
                    bucket=bucket,
                    local_file_path=p,
                    cos_key=key,
                )
            )

        for fut in as_completed(futures):
            try:
                key, size = fut.result()
                ok += 1
                total_bytes += int(size)
                print(f"[OK] {key}")
            except Exception as e:
                fail += 1
                print(f"[FAIL] {e}")

    print(f"[DONE] ok={ok} fail={fail} uploaded={total_bytes/1024/1024:.2f}MB month={month}")

    if fail > 0:
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="每日增量上传本月 factor+label+meta 到 COS（内网 endpoint 友好）")
    parser.add_argument("--local-root", required=True, help="本地导出根目录，例如 artifacts/factor_export_parquet")
    parser.add_argument("--cos-root", required=True, help="COS 目标前缀，例如 factor_export_parquet")
    parser.add_argument("--bucket", required=True, help="Bucket 名称（不含 cos:// 前缀）")
    parser.add_argument("--universe", default=os.getenv("UNIVERSE", "ZZ500"), help="领域，例如 ZZ500")
    parser.add_argument("--month", default=os.getenv("MONTH", ""), help="目标月份 YYYY-MM，默认取当前月")
    parser.add_argument("--region", default=os.getenv("COS_REGION", "ap-shanghai"), help="COS region，例如 ap-shanghai")
    parser.add_argument("--endpoint", default=os.getenv("COS_ENDPOINT", ""), help="COS endpoint（内网推荐 *.cos-internal.*），不填则走默认")
    parser.add_argument("--secret-id", default=os.getenv("COS_SECRET_ID", ""), help="SecretId（建议用环境变量 COS_SECRET_ID）")
    parser.add_argument("--secret-key", default=os.getenv("COS_SECRET_KEY", ""), help="SecretKey（建议用环境变量 COS_SECRET_KEY）")
    parser.add_argument("--workers", type=int, default=int(os.getenv("COS_WORKERS", "8")), help="并发上传线程数")
    parser.add_argument("--strict", action="store_true", help="严格模式：任何缺文件都报错退出（默认不严格）")
    args = parser.parse_args()

    month = (args.month or "").strip()
    if not month:
        month = datetime.now().strftime("%Y-%m")

    if not args.secret_id or not args.secret_key:
        raise SystemExit("缺少密钥：请通过 --secret-id/--secret-key 或环境变量 COS_SECRET_ID/COS_SECRET_KEY 传入")

    endpoint = args.endpoint.strip()
    if not endpoint:
        raise SystemExit("缺少 endpoint：请通过 --endpoint 或环境变量 COS_ENDPOINT 传入（内网上传必须）")

    run_upload_daily(
        local_root=Path(args.local_root),
        cos_root=str(args.cos_root),
        bucket=str(args.bucket),
        secret_id=str(args.secret_id),
        secret_key=str(args.secret_key),
        region=str(args.region),
        endpoint=endpoint,
        universe=str(args.universe).strip(),
        month=month,
        workers=int(args.workers),
        strict=bool(args.strict),
    )


if __name__ == "__main__":
    main()

