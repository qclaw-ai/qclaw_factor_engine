#!/usr/bin/env bash

set -euo pipefail

# 日内因子 pipeline：
# 1) stock_daily + 交易日历同步
# 2) 日更因子 bundle（factor_values_parquet/daily/.../factors.parquet + manifest）
# 3) 日更 → 年度 Parquet 回补（daily_parquet_merge_to_yearly_runner，upsert yearly_parquet）
# 对外 Parquet 交付：请用全链路里的 bin/run_cos_upload_daily.sh（或单独跑），本脚本不再 rsync。
#
# 脚本所在目录 -> 仓库根
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/daily_factor_pipeline_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

TRADE_DATE="${1:-$(date +%F)}"

ENV="${ENV:-prod}"
export ENV

CONFIG_FILE="${CONFIG_FILE:-config.ini}"

# 与 daily / merge 对齐：DAILY_UNIVERSE 优先，否则 UNIVERSE；皆空则不传 --universe（读 ini [daily].universe）
OPTIONAL_UNIVERSE=()
if [ -n "${DAILY_UNIVERSE:-}" ]; then
  OPTIONAL_UNIVERSE=( --universe "${DAILY_UNIVERSE}" )
elif [ -n "${UNIVERSE:-}" ]; then
  OPTIONAL_UNIVERSE=( --universe "${UNIVERSE}" )
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 开始, T=${TRADE_DATE}, config=${CONFIG_FILE}" >> "${LOG_FILE}"

# 1) 同步 stock_daily + Calendar 到 db_factor
# 说明：避免使用 "\" 续行（Windows CRLF 容易引入不可见参数导致 argparse 报错）
cmd_ingest=(
  "${PYTHON_BIN}" src/data_ingest/daily_stock_and_calendar_sync.py
  --config "${CONFIG_FILE}"
  --trade-date "${TRADE_DATE}"
  --lookback-days 5
  --calendar-buffer-days 10
)

# 需要显式捕获退出码，避免 stock_daily 同步失败直接被 set -e 中断
set +e

"${cmd_ingest[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

# 2) 跑日更因子值（bundle Parquet）
cmd_daily=(
  "${PYTHON_BIN}" src/daily_factor_values/daily_factor_values_runner.py
  --config "${CONFIG_FILE}"
  --trade-date "${TRADE_DATE}"
  --scope all_in_basic
  "${OPTIONAL_UNIVERSE[@]}"
)

# 说明：
# - 脚本顶部 set -e 会让任意一步失败直接退出，后面拿不到 $? 做统一收口
# - 这里显式捕获每一步退出码，便于日志收口

if [ ${EXIT_CODE} -eq 0 ]; then
  "${cmd_daily[@]}" >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

# 3) 日更 bundle → yearly_parquet（与 export/回测读 yearly 对齐）
cmd_merge=(
  "${PYTHON_BIN}" src/daily_factor_values/daily_parquet_merge_to_yearly_runner.py
  --config "${CONFIG_FILE}"
  --trade-date "${TRADE_DATE}"
  "${OPTIONAL_UNIVERSE[@]}"
)

if [ ${EXIT_CODE} -eq 0 ]; then
  "${cmd_merge[@]}" >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}
