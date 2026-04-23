#!/usr/bin/env bash

set -euo pipefail

# 脚本所在目录 -> 仓库根
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/pipeline_job_worker_cron_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

# 说明：
# - ENV 默认 prod，非 prod 会自动走 *_dev.ini（见 common.config.Config）
# - CONFIG_FILE 可通过环境变量或第1参数传入
ENV="${ENV:-prod}"
export ENV

CONFIG_FILE="${1:-${CONFIG_FILE:-config.ini}}"
RUNNING_TIMEOUT_MINUTES="${RUNNING_TIMEOUT_MINUTES:-30}"

# 防并发：同一时刻只允许一个 cron worker 在跑，避免重复启动浪费资源
LOCK_DIR="${PROJECT_ROOT}/logs/.pipeline_job_worker.lock"
if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - worker 已在运行，跳过本轮" >> "${LOG_FILE}"
  exit 0
fi
trap 'rmdir "${LOCK_DIR}" >/dev/null 2>&1 || true' EXIT

echo "$(date '+%Y-%m-%d %H:%M:%S') - pipeline_job worker 开始, config=${CONFIG_FILE}, timeout=${RUNNING_TIMEOUT_MINUTES}m" >> "${LOG_FILE}"

cmd_worker=(
  "${PYTHON_BIN}" run_pipeline_job_worker.py
  --config "${CONFIG_FILE}"
  --once
  --running-timeout-minutes "${RUNNING_TIMEOUT_MINUTES}"
)

set +e
"${cmd_worker[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?
set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - pipeline_job worker 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - pipeline_job worker 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

