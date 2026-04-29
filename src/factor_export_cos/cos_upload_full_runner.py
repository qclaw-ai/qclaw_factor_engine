from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional

from common.config import Config
from common.utils import setup_logger
from factor_export_cos.cos_upload import run_upload_full

logger = setup_logger("cos_upload_full_runner", "logs/cos_upload_full_runner.log")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="全量递归上传 factor_export_parquet 到 COS（内网 endpoint 友好）"
    )
    parser.add_argument("--config", default="config.ini", help="根配置文件路径")
    parser.add_argument(
        "--local-root",
        help=(
            "本地导出根目录，例如 artifacts/factor_export_parquet（默认读 "
            "[cos_factor_export].local_root，回退 [factor_export].output_root）"
        ),
    )
    parser.add_argument(
        "--cos-root",
        help=(
            "COS 目标前缀，例如 factor_export_parquet（默认读 "
            "[cos_factor_export].cos_root）"
        ),
    )
    parser.add_argument(
        "--bucket",
        help="Bucket 名称（不含 cos:// 前缀，默认读 [cos_factor_export].bucket）",
    )
    parser.add_argument(
        "--region",
        help="COS region，例如 ap-shanghai（默认读 [cos_factor_export].region）",
    )
    parser.add_argument(
        "--endpoint",
        help=(
            "COS endpoint（推荐 *.cos-internal.*，默认读 "
            "[cos_factor_export].endpoint）"
        ),
    )
    parser.add_argument(
        "--secret-id",
        help="SecretId（默认读 [cos_factor_export].secret_id）",
    )
    parser.add_argument(
        "--secret-key",
        help="SecretKey（默认读 [cos_factor_export].secret_key）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        help="并发上传线程数（默认读 [cos_factor_export].workers）",
    )
    parser.add_argument(
        "--include-suffix",
        action="append",
        default=[],
        help="可选：仅上传指定后缀（可重复），例如 --include-suffix .parquet",
    )
    args = parser.parse_args()

    cfg = Config(config_file=args.config)
    section = "cos_factor_export"

    logger.info(
        "runner start config_file=%s section=%s include_suffix=%s",
        args.config,
        section,
        args.include_suffix,
    )

    local_root = args.local_root
    if not local_root:
        local_root = cfg.get(
            section,
            "local_root",
            fallback=cfg.get(
                "factor_export",
                "output_root",
                fallback="artifacts/factor_export_parquet",
            ),
        )

    cos_root = args.cos_root or cfg.get(section, "cos_root", fallback="factor_export_parquet")
    bucket = args.bucket or cfg.get(section, "bucket", fallback="")
    region = args.region or cfg.get(section, "region", fallback="ap-shanghai")
    endpoint = (args.endpoint or "").strip() or cfg.get(section, "endpoint", fallback="")
    secret_id = args.secret_id or cfg.get(section, "secret_id", fallback="")
    secret_key = args.secret_key or cfg.get(section, "secret_key", fallback="")

    try:
        workers = int(
            args.workers
            if args.workers is not None
            else cfg.getint(section, "workers", fallback=8)
        )
    except ValueError:
        workers = 8

    if not bucket:
        raise SystemExit("缺少 bucket：请在 config.ini [cos_factor_export].bucket 或 CLI --bucket 中配置")

    if not secret_id or not secret_key:
        raise SystemExit(
            "缺少密钥：请在 config.ini [cos_factor_export].secret_id/secret_key "
            "或 CLI --secret-id/--secret-key 中配置"
        )

    if not endpoint:
        raise SystemExit(
            "缺少 endpoint：请在 config.ini [cos_factor_export].endpoint 或 "
            "CLI --endpoint 中配置（内网上传必须）"
        )

    logger.info(
        "runner resolved local_root=%s cos_root=%s bucket=%s region=%s endpoint=%s workers=%s",
        local_root,
        cos_root,
        bucket,
        region,
        endpoint,
        workers,
    )

    run_upload_full(
        local_root=Path(local_root),
        cos_root=str(cos_root),
        bucket=str(bucket),
        secret_id=str(secret_id),
        secret_key=str(secret_key),
        region=str(region),
        endpoint=endpoint,
        workers=int(workers),
        include_suffixes=list(args.include_suffix) if args.include_suffix else None,
    )


if __name__ == "__main__":
    main()

