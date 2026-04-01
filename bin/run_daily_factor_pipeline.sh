#!/usr/bin/env bash

set -euo pipefail

# 脚本所在目录 -> 仓库根
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/daily_factor_pipeline_$(date +\"%Y%m%d\").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

TRADE_DATE="${1:-$(date +%F)}"

ENV="${ENV:-prod}"
export ENV

echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 开始, T=${TRADE_DATE}" >> "${LOG_FILE}"

# 1) 同步 stock_daily + Calendar 到 db_factor
# 说明：避免使用 "\" 续行（Windows CRLF 容易引入不可见参数导致 argparse 报错）
cmd_ingest=(
  "${PYTHON_BIN}" src/data_ingest/daily_stock_and_calendar_sync.py
  --trade-date "${TRADE_DATE}"
  --lookback-days 380
  --calendar-buffer-days 10
)
"${cmd_ingest[@]}" >> "${LOG_FILE}" 2>&1

# 2) 跑日更因子值（ALL 域）
cmd_daily=(
  "${PYTHON_BIN}" src/daily_factor_values/daily_factor_values_runner.py
  --trade-date "${TRADE_DATE}"
  --universe ALL
  --scope all_in_basic
)
"${cmd_daily[@]}" >> "${LOG_FILE}" 2>&1

EXIT_CODE=$?

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

