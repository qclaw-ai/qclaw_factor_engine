#!/usr/bin/env bash

set -euo pipefail

# 每天：全链路（行情同步 + 日更因子 + Parquet 外发 + 校验 + 可选同步 COS）
#
# 设计原则：
# - 复用已有脚本：run_daily_factor_pipeline.sh + run_daily_parquet_export_pipeline.sh
# - 不直接在 cron 里写一串 python 命令，统一入口、统一日志、统一环境变量
# - 可选 COS 同步通过 COS_SYNC_CMD 注入（见 bin/run_cos_sync_factor_export_parquet.sh）

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/daily_full_pipeline_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

ENV="${ENV:-prod}"
export ENV

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"
export PYTHON_BIN

UNIVERSE="${UNIVERSE:-ZZ500}"
export UNIVERSE

# 对外导出默认当月；也允许手动覆盖
MONTH="${MONTH:-$(date +%Y-%m)}"
export MONTH

TRADE_DATE="${1:-$(date +%F)}"

# 是否在末尾同步 COS（默认关闭）
# - ENABLE_COS_UPLOAD：走 COS SDK 上传（bin/run_cos_upload_daily.sh）
# - ENABLE_COS_SYNC：走通用壳（bin/run_cos_sync_factor_export_parquet.sh，适合 coscmd/rclone）
ENABLE_COS_UPLOAD="${ENABLE_COS_UPLOAD:-0}"
ENABLE_COS_SYNC="${ENABLE_COS_SYNC:-0}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - daily full pipeline 开始, T=${TRADE_DATE}, universe=${UNIVERSE}, month=${MONTH}, ENV=${ENV}" >> "${LOG_FILE}"

set +e

# 1) 日内因子 pipeline（含行情+calendar同步 + 日更因子 + 可选 rsync 发布）
#
# 注意：该脚本内部日志写到自己的 daily_factor_pipeline_*.log；这里也保留一份汇总日志
bin/run_daily_factor_pipeline.sh "${TRADE_DATE}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

# 2) 当月对外 Parquet 导出 + validate（factor + label）
if [ ${EXIT_CODE} -eq 0 ]; then
  bin/run_daily_parquet_export_pipeline.sh >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

# 3) 可选：同步 COS（需要 COS_SYNC_CMD 或参数）
if [ ${EXIT_CODE} -eq 0 ] && ( [ "${ENABLE_COS_SYNC}" = "1" ] || [ "${ENABLE_COS_SYNC}" = "true" ] ); then
  bin/run_cos_sync_factor_export_parquet.sh >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

# 4) 可选：SDK 上传 COS（每日增量）
if [ ${EXIT_CODE} -eq 0 ] && ( [ "${ENABLE_COS_UPLOAD}" = "1" ] || [ "${ENABLE_COS_UPLOAD}" = "true" ] ); then
  bin/run_cos_upload_daily.sh >> "${LOG_FILE}" 2>&1
  EXIT_CODE=$?
fi

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - daily full pipeline 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - daily full pipeline 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

