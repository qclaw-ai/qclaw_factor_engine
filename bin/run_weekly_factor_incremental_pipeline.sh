#!/usr/bin/env bash

set -euo pipefail

# 每周：因子增量编排（factor_incremental_runner -> factor_engine 计算 batch_csv）
#
# 默认行为：
# - CONFIG_FILE=config.ini（由 common.Config 根据 ENV 自动切换 *_dev.ini）
# - MODE=incremental
# - AS_OF_DATE=今天（YYYY-MM-DD）
# - STAGE=candidate（可选覆盖）
#
# 你可以在 cron 或手动调用时覆盖：
# - CONFIG_FILE
# - MODE（incremental|rebase）
# - AS_OF_DATE
# - STAGE（candidate|production|deprecated）
# - BATCH_ID
# - WARMUP_TRADING_DAYS

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/weekly_factor_incremental_pipeline_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

ENV="${ENV:-prod}"
export ENV

CONFIG_FILE="${CONFIG_FILE:-config.ini}"
MODE="${MODE:-incremental}"
AS_OF_DATE="${AS_OF_DATE:-$(date +%F)}"

# 可选项：只在非空时追加到命令行
STAGE="${STAGE:-}"
BATCH_ID="${BATCH_ID:-}"
WARMUP_TRADING_DAYS="${WARMUP_TRADING_DAYS:-}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - weekly factor incremental pipeline 开始 ENV=${ENV}" >> "${LOG_FILE}"
echo "$(date '+%Y-%m-%d %H:%M:%S') - configs: CONFIG_FILE=${CONFIG_FILE} MODE=${MODE} AS_OF_DATE=${AS_OF_DATE} STAGE=${STAGE}" >> "${LOG_FILE}"

set +e

cmd=(
  "${PYTHON_BIN}" src/factor_incremental/factor_incremental_runner.py
  --config "${CONFIG_FILE}"
  --as-of-date "${AS_OF_DATE}"
  --mode "${MODE}"
)

if [ -n "${STAGE}" ]; then
  cmd+=( --stage "${STAGE}" )
fi

if [ -n "${BATCH_ID}" ]; then
  cmd+=( --batch-id "${BATCH_ID}" )
fi

if [ -n "${WARMUP_TRADING_DAYS}" ]; then
  cmd+=( --warmup-trading-days "${WARMUP_TRADING_DAYS}" )
fi

"${cmd[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - weekly factor incremental pipeline 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - weekly factor incremental pipeline 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

