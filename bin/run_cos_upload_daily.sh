#!/usr/bin/env bash

set -euo pipefail

# 每日增量上传：只上传“本月” factor+label parts + manifest + watermark 到 COS（内网 endpoint）
#
# 依赖：
# - scripts/upload_factor_export_parquet_daily.py
# - requirements.txt 已包含 cos-python-sdk-v5
#
# 主要通过环境变量配置：
# - COS_SECRET_ID / COS_SECRET_KEY
# - COS_REGION（默认 ap-shanghai）
# - COS_ENDPOINT（必须，内网 endpoint）
# - COS_BUCKET（必须）
# - COS_ROOT（默认 factor_export_parquet）
# - LOCAL_ROOT（默认 artifacts/factor_export_parquet）
# - UNIVERSE（默认 ZZ500）
# - MONTH（默认当前月 YYYY-MM）
# - COS_WORKERS（默认 8）
#
# 安全建议：
# - 不要把 COS_SECRET_ID / COS_SECRET_KEY 写死在脚本里。
# - 推荐通过环境变量注入，或使用只读权限的 env 文件（chmod 600），再由本脚本 source。
#   - 使用方式：export COS_ENV_FILE=/etc/qclaw/cos.env
#
# 你明确要求“写在 sh 里配置”：
# - 请仅在云端私有机器修改此文件（不要提交到 git）
# - 把下面两行替换成你的真实密钥
# - 若你仍希望走 env 文件，可继续用 COS_ENV_FILE（source 会覆盖同名变量）

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"
cd "${PROJECT_ROOT}"

mkdir -p "${PROJECT_ROOT}/logs"

LOG_FILE="${PROJECT_ROOT}/logs/cos_upload_daily_$(date +"%Y%m%d").log"

export PYTHONIOENCODING=utf-8
export LANG=en_US.UTF-8
export PYTHONPATH="${PROJECT_ROOT}/src"

ENV="${ENV:-prod}"
export ENV

# Miniconda Python（cron 下 PATH 常不含 python；可用环境变量 PYTHON_BIN 覆盖）
PYTHON_BIN="${PYTHON_BIN:-/home/ubuntu/miniconda3/bin/python}"

# ---------------------- COS 密钥（按需填入） ----------------------
# 重要：不要把真实密钥提交到 git。只在云端私有机器改这两行。
COS_SECRET_ID="${COS_SECRET_ID:-你的子账号SecretId}"
COS_SECRET_KEY="${COS_SECRET_KEY:-你的子账号SecretKey}"
export COS_SECRET_ID
export COS_SECRET_KEY
# -----------------------------------------------------------------

COS_ENV_FILE="${COS_ENV_FILE:-}"
if [ -n "${COS_ENV_FILE}" ] && [ -f "${COS_ENV_FILE}" ]; then
  # shellcheck disable=SC1090
  source "${COS_ENV_FILE}"
fi

LOCAL_ROOT="${LOCAL_ROOT:-artifacts/factor_export_parquet}"
COS_ROOT="${COS_ROOT:-factor_export_parquet}"
COS_BUCKET="${COS_BUCKET:-}"
COS_REGION="${COS_REGION:-ap-shanghai}"
COS_ENDPOINT="${COS_ENDPOINT:-}"
COS_WORKERS="${COS_WORKERS:-8}"

UNIVERSE="${UNIVERSE:-ZZ500}"
MONTH="${MONTH:-$(date +%Y-%m)}"

STRICT="${STRICT:-0}"

echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload daily 开始 universe=${UNIVERSE} month=${MONTH} local_root=${LOCAL_ROOT} cos_root=${COS_ROOT}" >> "${LOG_FILE}"

if [ -z "${COS_BUCKET}" ] || [ -z "${COS_ENDPOINT}" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - 缺少 COS_BUCKET 或 COS_ENDPOINT，退出" >> "${LOG_FILE}"
  exit 2
fi

set +e

cmd=(
  "${PYTHON_BIN}" scripts/upload_factor_export_parquet_daily.py
  --local-root "${LOCAL_ROOT}"
  --cos-root "${COS_ROOT}"
  --bucket "${COS_BUCKET}"
  --region "${COS_REGION}"
  --endpoint "${COS_ENDPOINT}"
  --workers "${COS_WORKERS}"
  --universe "${UNIVERSE}"
  --month "${MONTH}"
)
if [ "${STRICT}" = "1" ] || [ "${STRICT}" = "true" ]; then
  cmd+=( --strict )
fi

"${cmd[@]}" >> "${LOG_FILE}" 2>&1
EXIT_CODE=$?

set -euo pipefail

if [ ${EXIT_CODE} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload daily 成功结束" >> "${LOG_FILE}"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') - cos upload daily 失败, code=${EXIT_CODE}" >> "${LOG_FILE}"
fi

exit ${EXIT_CODE}

