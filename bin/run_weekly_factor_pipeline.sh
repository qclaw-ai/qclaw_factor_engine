#!/usr/bin/env bash

set -euo pipefail

# 每周：因子工厂大回测全链路（factor_engine -> backtest_io -> selection_and_store -> factor_corr）

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/weekly_factor_pipeline_$(date +\"%Y%m%d\").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

ENV="${ENV:-prod}"
export ENV

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

SYNC_LEGACY_PATHS="${SYNC_LEGACY_PATHS:-0}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - weekly factor pipeline 开始, ENV=${ENV}" >> "${LOG_FILE}"
echo "$(date '+%Y-%m-%d %H:%M:%S') - configs: 使用各模块默认 config.ini（由 common.Config 按 ENV 自动切换 *_dev.ini）" >> "${LOG_FILE}"

# 1) 因子引擎：生成 batch_csv（factor_value_files: artifact_type=batch_csv）
"${PYTHON_BIN}" src/factor_engine/factor_engine_runner.py >> "${LOG_FILE}" 2>&1


# 2) 回测 IO：调用 backtest_core 计算指标，并写入 factor_backtest + 回测 JSON
"${PYTHON_BIN}" src/backtest_io/backtest_io_runner.py >> "${LOG_FILE}" 2>&1


# 3) 选入库：根据阈值打 pass_standard，并同步 factor_basic.is_valid / factor_files.backtest_json_path
"${PYTHON_BIN}" src/selection_and_store/selection_and_store_runner.py >> "${LOG_FILE}" 2>&1


# 4) 相关性：对 is_valid=true 的因子计算 corr matrix 写入 Redis
"${PYTHON_BIN}" src/factor_corr/factor_corr_matrix.py >> "${LOG_FILE}" 2>&1


# 5) （可选）兼容：同步 factor_value_files(batch_csv) 到 factor_files.factor_values_path（旧脚本过渡用）
if [ "${SYNC_LEGACY_PATHS}" = "1" ] || [ "${SYNC_LEGACY_PATHS}" = "true" ]; then
  "${PYTHON_BIN}" src/backtest_io/sync_factor_values_path_runner.py >> "${LOG_FILE}" 2>&1
fi

EXIT_CODE=$?

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - weekly factor pipeline 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - weekly factor pipeline 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

