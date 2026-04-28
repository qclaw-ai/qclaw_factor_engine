#!/usr/bin/env bash

set -euo pipefail

# 同步导出目录到 COS（占位脚本）
#
# 说明：
# - 目前仓库未内置 coscmd/腾讯云 CLI/SDK 的标准化同步脚本，因此这里先给一个可复用的入口壳。
# - 你可以选择：
#   A) 使用 coscmd：coscmd sync <local> <cos://bucket/prefix> -r
#   B) 使用腾讯云 CLI：tccli cos ...
#   C) 使用 rclone：rclone sync <local> <remote:bucket/prefix>
#
# 使用方式：
# - 通过环境变量注入同步命令（推荐），例如：
#   COS_SYNC_CMD='coscmd sync artifacts/factor_export_parquet cos://xxx/factor_export_parquet -r' bin/run_cos_sync_factor_export_parquet.sh
#
# - 或者通过第 1 个参数传入整条命令：
#   bin/run_cos_sync_factor_export_parquet.sh "coscmd sync artifacts/factor_export_parquet cos://xxx/factor_export_parquet -r"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/cos_sync_factor_export_parquet_$(date +"%Y%m%d").log"

export LANG=en_US.UTF-8

SYNC_CMD="${1:-${COS_SYNC_CMD:-}}"
if [ -z "${SYNC_CMD}" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 缺少 COS_SYNC_CMD，未执行同步" >> "${LOG_FILE}"
  echo "Usage:"
  echo "  COS_SYNC_CMD='coscmd sync artifacts/factor_export_parquet cos://<bucket>/<prefix> -r' bin/run_cos_sync_factor_export_parquet.sh"
  echo "  bin/run_cos_sync_factor_export_parquet.sh \"coscmd sync artifacts/factor_export_parquet cos://<bucket>/<prefix> -r\""
  exit 2
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') - COS sync 开始 cmd=${SYNC_CMD}" >> "${LOG_FILE}"

set +e
bash -lc "${SYNC_CMD}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?
set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - COS sync 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - COS sync 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

