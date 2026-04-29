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
  --scope all_in_basic
)
#
# 说明：
# - 脚本顶部 set -e 会让任意一步失败直接退出，后面拿不到 $? 做统一收口
# - 这里显式捕获每一步退出码，保证日志/发布（rsync）行为可控
set +e

"${cmd_ingest[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

if [ ${EXIT_CODE} -eq 0 ]; then
  "${cmd_daily[@]}" >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

# 3) 可选：发布产出到目标机（rsync + ssh）；默认关闭（ENABLE_RSYNC_PUBLISH=1 或 true 时执行）
# - 路径可通过 RSYNC_SRC / RSYNC_DEST 覆盖；仅在前序步骤成功时才推送
ENABLE_RSYNC_PUBLISH="${ENABLE_RSYNC_PUBLISH:-0}"
RSYNC_SRC="${RSYNC_SRC:-/data/qclaw/qclaw_factor_engine/factor_values/}"
RSYNC_DEST="${RSYNC_DEST:-ubuntu@10.1.0.5:/data/factor/}"

if [ ${EXIT_CODE} -eq 0 ]; then
  if [ "${ENABLE_RSYNC_PUBLISH}" = "1" ] || [ "${ENABLE_RSYNC_PUBLISH}" = "true" ]; then
    rsync -avz "${RSYNC_SRC}" "${RSYNC_DEST}" >> "${LOG_FILE}" 2>&1
    EXIT_CODE=$?
  else
    echo "$(date '+%Y-%m-%d %H:%M:%S') - 跳过 rsync 发布（ENABLE_RSYNC_PUBLISH=${ENABLE_RSYNC_PUBLISH}）" >> "${LOG_FILE}"
  fi
fi

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 日内因子 pipeline 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

