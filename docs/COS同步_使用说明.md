# COS 同步使用说明（内网 Endpoint）

> 目标：把 `artifacts/factor_export_parquet/` 同步到 COS，供客户从 COS 下载训练数据。

---

## 1. 依赖

仓库已在 `requirements.txt` 增加：

- `cos-python-sdk-v5`

安装（base / conda 环境均可）：

```bash
/home/ubuntu/miniconda3/bin/python -m pip install -r requirements.txt
```

---

## 2. 你需要准备的参数（建议用环境变量）

- `COS_SECRET_ID`
- `COS_SECRET_KEY`
- `COS_REGION`（默认 `ap-shanghai`）
- `COS_ENDPOINT`（内网 endpoint，形如：`<bucket>.cos-internal.<region>.myqcloud.com`）

也支持用 env 文件统一管理（避免把密钥写进 crontab）：

- `COS_ENV_FILE`：例如 `/etc/qclaw/cos.env`（建议 `chmod 600`）
- 文件内容示例：

```bash
export COS_SECRET_ID=...
export COS_SECRET_KEY=...
export COS_REGION=ap-shanghai
export COS_ENDPOINT=factor-data-1324221249.cos-internal.ap-shanghai.myqcloud.com
export COS_BUCKET=factor-data-1324221249
export COS_ROOT=factor_export_parquet
export LOCAL_ROOT=artifacts/factor_export_parquet
```

Bucket 与前缀：

- `BUCKET`：例如 `factor-data-1324221249`
- `COS_ROOT`：例如 `factor_export_parquet`

本地根目录：

- `LOCAL_ROOT`：例如 `artifacts/factor_export_parquet`

---

## 3. 两个脚本怎么选

### 3.1 全量同步（历史回填/首次上线用）

脚本：`scripts/upload_factor_export_parquet_full.py`

特点：

- 递归上传 `LOCAL_ROOT` 下所有文件
- 保留目录结构：Key = `COS_ROOT/<relative_path>`

示例：

```bash
export COS_SECRET_ID=...
export COS_SECRET_KEY=...
export COS_REGION=ap-shanghai
export COS_ENDPOINT=factor-data-1324221249.cos-internal.ap-shanghai.myqcloud.com

/home/ubuntu/miniconda3/bin/python scripts/upload_factor_export_parquet_full.py \
  --local-root artifacts/factor_export_parquet \
  --cos-root factor_export_parquet \
  --bucket factor-data-1324221249
```

### 3.2 每日增量同步（每天跑完导出后用）

脚本：`scripts/upload_factor_export_parquet_daily.py`

特点：

- 只上传“本月”的 factor+label parts + 本月 manifest + 最新 watermark
- 非常适合每天跑：速度快、成本低

会上传这些路径（本地相对 `LOCAL_ROOT`）：

- `factor/universe=U/month=YYYY-MM/part-*.parquet`
- `label/universe=U/month=YYYY-MM/part-*.parquet`
- `meta/manifest/factor/U/YYYY-MM.json`
- `meta/manifest/label/U/YYYY-MM.json`
- `meta/watermark/factor/U.json`
- `meta/watermark/label/U.json`

示例：

```bash
export COS_SECRET_ID=...
export COS_SECRET_KEY=...
export COS_REGION=ap-shanghai
export COS_ENDPOINT=factor-data-1324221249.cos-internal.ap-shanghai.myqcloud.com

/home/ubuntu/miniconda3/bin/python scripts/upload_factor_export_parquet_daily.py \
  --local-root artifacts/factor_export_parquet \
  --cos-root factor_export_parquet \
  --bucket factor-data-1324221249 \
  --universe ZZ500 \
  --month 2026-04
```

---

## 4. 与 `bin/*.sh` 怎么集成

你现在有：

- `bin/run_daily_full_pipeline.sh`：每天跑完“行情 + 日更因子 + Parquet 导出 + validate”

建议在每天 pipeline 成功后追加一步：

- 调用 `bin/run_cos_upload_daily.sh`（内部会调用 `scripts/upload_factor_export_parquet_daily.py` 把本月最新导出推到 COS）

也可以在首次历史回填后调用一次：

- `bin/run_cos_upload_full.sh`（内部会调用 `scripts/upload_factor_export_parquet_full.py` 做全量上传）

> 说明：仓库里也有一个通用的 COS 同步壳脚本 `bin/run_cos_sync_factor_export_parquet.sh`，但它更适合你用 coscmd/rclone 时注入命令；如果你决定统一用 SDK，就直接在 cron 或 pipeline 里调用这里的 python 脚本即可。
