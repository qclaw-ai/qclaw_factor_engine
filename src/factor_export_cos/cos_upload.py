from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from qcloud_cos import CosConfig, CosS3Client

from common.utils import setup_logger

logger = setup_logger("cos_upload", "logs/cos_upload.log")


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

    factor_dir = local_root / "factor" / f"universe={universe}" / f"month={month}"
    if factor_dir.exists():
        paths.extend(sorted(factor_dir.glob("part-*.parquet")))

    label_dir = local_root / "label" / f"universe={universe}" / f"month={month}"
    if label_dir.exists():
        paths.extend(sorted(label_dir.glob("part-*.parquet")))

    paths.append(local_root / "meta" / "manifest" / "factor" / universe / f"{month}.json")
    paths.append(local_root / "meta" / "manifest" / "label" / universe / f"{month}.json")

    paths.append(local_root / "meta" / "watermark" / "factor" / f"{universe}.json")
    paths.append(local_root / "meta" / "watermark" / "label" / f"{universe}.json")

    uniq: List[Path] = []
    seen = set()
    for p in paths:
        rp = str(p.resolve())
        if rp in seen:
            continue
        seen.add(rp)
        if p.exists() and p.is_file():
            uniq.append(p)

    return uniq


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

    logger.info(
        "upload_full start local_root=%s bucket=%s cos_root=%s endpoint=%s region=%s workers=%s include_suffixes=%s",
        local_root,
        bucket,
        cos_root,
        endpoint,
        region,
        workers,
        include_suffixes,
    )

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
        if suffixes is not None and p.suffix.lower() not in suffixes:
            continue
        files.append(p)

    if not files:
        logger.warning("upload_full skip: no files found local_root=%s", local_root)
        return

    logger.info("upload_full collected files=%s", len(files))

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
                    logger.info(
                        "upload_full progress ok=%s fail=%s uploaded=%.2fMB latest=%s",
                        ok,
                        fail,
                        total_bytes / 1024 / 1024,
                        key,
                    )
            except Exception as e:
                fail += 1
                logger.error("upload_full failed err=%s", e, exc_info=True)

    logger.info(
        "upload_full done ok=%s fail=%s uploaded=%.2fMB",
        ok,
        fail,
        total_bytes / 1024 / 1024,
    )

    if fail > 0:
        raise SystemExit(2)


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

    logger.info(
        "upload_daily start local_root=%s bucket=%s cos_root=%s endpoint=%s region=%s workers=%s universe=%s month=%s strict=%s",
        local_root,
        bucket,
        cos_root,
        endpoint,
        region,
        workers,
        universe,
        month,
        strict,
    )

    client = _build_client(
        secret_id=secret_id,
        secret_key=secret_key,
        region=region,
        endpoint=endpoint,
    )

    files = _collect_month_paths(local_root=local_root, universe=universe, month=month)
    if not files:
        msg = (
            f"未发现任何可上传文件 universe={universe} month={month} "
            f"local_root={local_root}"
        )
        if strict:
            raise SystemExit(msg)
        logger.warning("upload_daily skip: %s", msg)
        return

    logger.info("upload_daily collected files=%s", len(files))

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
                logger.info("upload_daily ok key=%s", key)
            except Exception as e:
                fail += 1
                logger.error("upload_daily failed err=%s", e, exc_info=True)

    logger.info(
        "upload_daily done ok=%s fail=%s uploaded=%.2fMB month=%s",
        ok,
        fail,
        total_bytes / 1024 / 1024,
        month,
    )

    if fail > 0:
        raise SystemExit(2)

