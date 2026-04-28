#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
全量同步：把本地导出目录（factor_export_parquet）递归上传到 COS（推荐内网 endpoint）。

设计目标：
- 不在代码里写死密钥：用 CLI 参数或环境变量传入；
- 保留本地目录结构（Key = cos_root + 相对路径）；
- 适配你当前导出树：
  - factor/...
  - label/...
  - meta/...
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from qcloud_cos import CosConfig, CosS3Client


def _iter_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*"):
        if p.is_file():
            yield p


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


def run_upload_full(
    *,
    local_root: Path,
    cos_root: str,
    bucket: str,
    secret_id: str,
    secret_key: str,
    region: str,
    endpoint: str,
    workers: int,
    include_suffixes: Optional[List[str]] = None,
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

    suffixes = None
    if include_suffixes:
        suffixes = {s.strip().lower() for s in include_suffixes if s and s.strip()}

    files: List[Path] = []
    for p in _iter_files(local_root):
        if suffixes is not None:
            if p.suffix.lower() not in suffixes:
                continue

        files.append(p)

    if not files:
        print(f"[SKIP] 未发现文件: {local_root}")
        return

    print(f"[INFO] upload_full local_root={local_root} files={len(files)} bucket={bucket} cos_root={cos_root} endpoint={endpoint}")

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
                if ok % 200 == 0:
                    print(f"[PROGRESS] ok={ok} fail={fail} uploaded={total_bytes/1024/1024:.2f}MB latest={key}")
            except Exception as e:
                fail += 1
                print(f"[FAIL] {e}")

    print(f"[DONE] ok={ok} fail={fail} uploaded={total_bytes/1024/1024:.2f}MB")

    if fail > 0:
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="全量递归上传 factor_export_parquet 到 COS（内网 endpoint 友好）")
    parser.add_argument("--local-root", required=True, help="本地导出根目录，例如 artifacts/factor_export_parquet")
    parser.add_argument("--cos-root", required=True, help="COS 目标前缀，例如 factor_export_parquet")
    parser.add_argument("--bucket", required=True, help="Bucket 名称（不含 cos:// 前缀）")
    parser.add_argument("--region", default=os.getenv("COS_REGION", "ap-shanghai"), help="COS region，例如 ap-shanghai")
    parser.add_argument("--endpoint", default=os.getenv("COS_ENDPOINT", ""), help="COS endpoint（内网推荐 *.cos-internal.*），不填则走默认")
    parser.add_argument("--secret-id", default=os.getenv("COS_SECRET_ID", ""), help="SecretId（建议用环境变量 COS_SECRET_ID）")
    parser.add_argument("--secret-key", default=os.getenv("COS_SECRET_KEY", ""), help="SecretKey（建议用环境变量 COS_SECRET_KEY）")
    parser.add_argument("--workers", type=int, default=int(os.getenv("COS_WORKERS", "8")), help="并发上传线程数")
    parser.add_argument("--include-suffix", action="append", default=[], help="可选：仅上传指定后缀（可重复），例如 --include-suffix .parquet")
    args = parser.parse_args()

    if not args.secret_id or not args.secret_key:
        raise SystemExit("缺少密钥：请通过 --secret-id/--secret-key 或环境变量 COS_SECRET_ID/COS_SECRET_KEY 传入")

    endpoint = args.endpoint.strip()
    if not endpoint:
        raise SystemExit("缺少 endpoint：请通过 --endpoint 或环境变量 COS_ENDPOINT 传入（内网上传必须）")

    run_upload_full(
        local_root=Path(args.local_root),
        cos_root=str(args.cos_root),
        bucket=str(args.bucket),
        secret_id=str(args.secret_id),
        secret_key=str(args.secret_key),
        region=str(args.region),
        endpoint=endpoint,
        workers=int(args.workers),
        include_suffixes=list(args.include_suffix) if args.include_suffix else None,
    )


if __name__ == "__main__":
    main()

