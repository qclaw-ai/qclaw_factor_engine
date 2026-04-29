#!/usr/bin/env bash

set -euo pipefail

# 全量上传：递归上传导出目录到 COS（推荐内网 endpoint）。
# COS 相关配置（bucket/endpoint/密钥等）统一从 config.ini 的 [cos_factor_export] 读取，
# 本脚本只负责拼接 Python 命令和写日志；如需临时覆盖，可在下方 cmd 追加参数。

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/cos_upload_full_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

ENV="${ENV:-prod}"
export ENV

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

# 可选：只上传指定后缀，例如 ".parquet" 或 ".json"
INCLUDE_SUFFIXES="${INCLUDE_SUFFIXES:-}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload full 开始（参数从 config.ini 读取）" >> "${LOG_FILE}"

set +e

cmd=(
  "${PYTHON_BIN}" -m factor_export_cos.cos_upload_full_runner
  --config config.ini
)

if [ -n "${INCLUDE_SUFFIXES}" ]; then
  # 以逗号分隔，例如 ".parquet,.json"
  IFS=',' read -r -a suffix_arr <<< "${INCLUDE_SUFFIXES}"
  for s in "${suffix_arr[@]}"; do
    ss="$(echo "${s}" | xargs)"
    if [ -n "${ss}" ]; then
      cmd+=( --include-suffix "${ss}" )
    fi
  done
fi

"${cmd[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload full 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload full 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}