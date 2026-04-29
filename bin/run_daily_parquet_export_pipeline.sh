#!/usr/bin/env bash

set -euo pipefail

# 每天：对外 Parquet 导出（factor + label）+ 校验
# - 默认导出“当月”（month=YYYY-MM，取当前日期）
# - 可选启用 factor include-daily patch（需已有 daily_csv）
# - 仅在导出成功后才继续校验，避免刷出误导性 PASS

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/daily_parquet_export_pipeline_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

ENV="${ENV:-prod}"
export ENV

UNIVERSE="${UNIVERSE:-ZZ500}"
MONTH="${MONTH:-$(date +%Y-%m)}"
STAGE="${STAGE:-candidate}"
OUTPUT_ROOT="${OUTPUT_ROOT:-artifacts/factor_export_parquet}"

# factor 是否启用 include-daily patch（默认关闭）
INCLUDE_DAILY="${INCLUDE_DAILY:-0}"
DAILY_RECENT_DAYS="${DAILY_RECENT_DAYS:-15}"
FACTOR_BATCH_SIZE="${FACTOR_BATCH_SIZE:-50}"
FACTOR_MAX_ROWS_PER_PART="${FACTOR_MAX_ROWS_PER_PART:-300000}"

# label 导出 SQL 末端缓冲天数（y_ret_5d 月末非空更依赖缓冲）
LABEL_SQL_END_BUFFER_DAYS="${LABEL_SQL_END_BUFFER_DAYS:-60}"
LABEL_MAX_ROWS_PER_PART="${LABEL_MAX_ROWS_PER_PART:-500000}"

SKIP_VALIDATE="${SKIP_VALIDATE:-0}"

# validate_factor_export：默认跳过 CSV 抽样对账；需要严格对账时设 SKIP_RECONCILE=0（与 monthly 回填语义一致）
SKIP_RECONCILE="${SKIP_RECONCILE:-1}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - daily parquet export pipeline 开始, universe=${UNIVERSE}, month=${MONTH}, stage=${STAGE}, ENV=${ENV}" >> "${LOG_FILE}"
echo "$(date '+%Y-%m-%d %H:%M:%S') - output_root=${OUTPUT_ROOT}, include_daily=${INCLUDE_DAILY}, daily_recent_days=${DAILY_RECENT_DAYS}, skip_validate=${SKIP_VALIDATE}, skip_reconcile=${SKIP_RECONCILE}" >> "${LOG_FILE}"

set +e

# 1) factor export
cmd_factor=(
  "${PYTHON_BIN}" src/factor_export_cos/factor_export_runner.py
  --config config.ini
  --universe "${UNIVERSE}"
  --month "${MONTH}"
  --stage "${STAGE}"
  --factor-batch-size "${FACTOR_BATCH_SIZE}"
  --max-rows-per-part "${FACTOR_MAX_ROWS_PER_PART}"
  --output-root "${OUTPUT_ROOT}"
)
if [ "${INCLUDE_DAILY}" = "1" ] || [ "${INCLUDE_DAILY}" = "true" ]; then
  cmd_factor+=( --include-daily --daily-recent-days "${DAILY_RECENT_DAYS}" )
fi

"${cmd_factor[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

# 2) label export
if [ ${EXIT_CODE} -eq 0 ]; then
  cmd_label=(
    "${PYTHON_BIN}" src/factor_export_cos/label_export_runner.py
    --config config.ini
    --universe "${UNIVERSE}"
    --month "${MONTH}"
    --sql-end-buffer-days "${LABEL_SQL_END_BUFFER_DAYS}"
    --max-rows-per-part "${LABEL_MAX_ROWS_PER_PART}"
    --output-root "${OUTPUT_ROOT}"
  )
  "${cmd_label[@]}" >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

# 3) validate
if [ ${EXIT_CODE} -eq 0 ] && ! ( [ "${SKIP_VALIDATE}" = "1" ] || [ "${SKIP_VALIDATE}" = "true" ] ); then
  cmd_validate_factor=(
    "${PYTHON_BIN}" scripts/validate_factor_export.py
    --output-root "${OUTPUT_ROOT}"
    --universe "${UNIVERSE}"
    --month "${MONTH}"
    --project-root "."
  )
  if [ "${SKIP_RECONCILE}" = "1" ] || [ "${SKIP_RECONCILE}" = "true" ]; then
    cmd_validate_factor+=( --skip-reconcile )
  fi
  "${cmd_validate_factor[@]}" >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?

  if [ ${EXIT_CODE} -eq 0 ]; then
    cmd_validate_label=(
      "${PYTHON_BIN}" scripts/validate_label_export.py
      --root "${OUTPUT_ROOT}"
      --universe "${UNIVERSE}"
      --month "${MONTH}"
    )
    "${cmd_validate_label[@]}" >> "${LOG_FILE}" 2>&1
    EXIT_CODE=$?
  fi
fi

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - daily parquet export pipeline 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - daily parquet export pipeline 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

