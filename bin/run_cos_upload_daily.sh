#!/usr/bin/env bash

set -euo pipefail

# 每日增量上传：只上传“本月” factor+label parts + manifest + watermark 到 COS（内网 endpoint）
# 依赖：
# - scripts/upload_factor_export_parquet_daily.py
# COS 相关配置（bucket/endpoint/密钥等）统一从 config.ini 的 [cos_factor_export] 读取，
# 本脚本只负责拼接 Python 命令和写日志；如需临时覆盖，可在下方 cmd 追加参数。

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/cos_upload_daily_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

ENV="${ENV:-prod}"
export ENV

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

UNIVERSE="${UNIVERSE:-ZZ500}"
MONTH="${MONTH:-$(date +%Y-%m)}"
STRICT="${STRICT:-0}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload daily 开始 universe=${UNIVERSE} month=${MONTH}（COS 参数从 config.ini 读取）" >> "${LOG_FILE}"

set +e

cmd=(
  "${PYTHON_BIN}" -m factor_export_cos.cos_upload_daily_runner
  --config config.ini
  --universe "${UNIVERSE}"
  --month "${MONTH}"
)

if [ "${STRICT}" = "1" ] || [ "${STRICT}" = "true" ]; then
  cmd+=( --strict )
fi

"${cmd[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload daily 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload daily 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}