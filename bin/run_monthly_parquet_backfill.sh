#!/usr/bin/env bash

set -euo pipefail

# 按月回填：Parquet 外发（factor + label）
# - 内部调用 scripts/bootstrap_factor_export_history.py 与 scripts/bootstrap_label_export_history.py
# - 适合首次全历史回填（2015-至今）或重跑失败月份

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/monthly_parquet_backfill_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

ENV="${ENV:-prod}"
export ENV

UNIVERSE="${UNIVERSE:-ZZ500}"
STAGE="${STAGE:-candidate}"
OUTPUT_ROOT="${OUTPUT_ROOT:-artifacts/factor_export_parquet}"

START_MONTH="${START_MONTH:-2015-01}"
END_MONTH="${END_MONTH:-$(date +%Y-%m)}"

# factor include-daily 默认关闭（历史回填通常不需要 daily patch）
INCLUDE_DAILY="${INCLUDE_DAILY:-0}"
DAILY_RECENT_DAYS="${DAILY_RECENT_DAYS:-15}"

FACTOR_BATCH_SIZE="${FACTOR_BATCH_SIZE:-50}"
FACTOR_MAX_ROWS_PER_PART="${FACTOR_MAX_ROWS_PER_PART:-300000}"

LABEL_SQL_END_BUFFER_DAYS="${LABEL_SQL_END_BUFFER_DAYS:-60}"
LABEL_MAX_ROWS_PER_PART="${LABEL_MAX_ROWS_PER_PART:-500000}"

STOP_ON_ERROR="${STOP_ON_ERROR:-1}"

# factor 回填校验：`validate_factor_export.py` 的 CSV 抽样对账失败时可跳过（不传则做全量校验）
SKIP_RECONCILE="${SKIP_RECONCILE:-1}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - monthly parquet backfill 开始 universe=${UNIVERSE} months=${START_MONTH}..${END_MONTH} ENV=${ENV} SKIP_RECONCILE=${SKIP_RECONCILE}" >> "${LOG_FILE}"

set +e

# 1) factor backfill
cmd_factor=(
  "${PYTHON_BIN}" scripts/bootstrap_factor_export_history.py
  --config config.ini
  --universe "${UNIVERSE}"
  --start-month "${START_MONTH}"
  --end-month "${END_MONTH}"
  --stage "${STAGE}"
  --factor-batch-size "${FACTOR_BATCH_SIZE}"
  --max-rows-per-part "${FACTOR_MAX_ROWS_PER_PART}"
  --output-root "${OUTPUT_ROOT}"
)
if [ "${INCLUDE_DAILY}" = "1" ] || [ "${INCLUDE_DAILY}" = "true" ]; then
  cmd_factor+=( --include-daily --daily-recent-days "${DAILY_RECENT_DAYS}" )
fi
if [ "${STOP_ON_ERROR}" = "1" ] || [ "${STOP_ON_ERROR}" = "true" ]; then
  cmd_factor+=( --stop-on-error )
fi
if [ "${SKIP_RECONCILE}" = "1" ] || [ "${SKIP_RECONCILE}" = "true" ]; then
  cmd_factor+=( --skip-reconcile )
fi

"${cmd_factor[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

# 2) label backfill
if [ ${EXIT_CODE} -eq 0 ]; then
  cmd_label=(
    "${PYTHON_BIN}" scripts/bootstrap_label_export_history.py
    --config config.ini
    --universe "${UNIVERSE}"
    --start-month "${START_MONTH}"
    --end-month "${END_MONTH}"
    --max-rows-per-part "${LABEL_MAX_ROWS_PER_PART}"
    --sql-end-buffer-days "${LABEL_SQL_END_BUFFER_DAYS}"
    --output-root "${OUTPUT_ROOT}"
  )
  if [ "${STOP_ON_ERROR}" = "1" ] || [ "${STOP_ON_ERROR}" = "true" ]; then
    cmd_label+=( --stop-on-error )
  fi
  "${cmd_label[@]}" >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - monthly parquet backfill 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - monthly parquet backfill 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

