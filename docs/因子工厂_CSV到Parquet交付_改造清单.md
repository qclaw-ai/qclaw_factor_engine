# 因子工厂：CSV 真源 + Parquet 对外交付 — 改造清单

> 目标：在**不破坏现有**因子计算、回测、增量逻辑的前提下，增加 **Parquet 外发**能力；单人可维护、可回滚。  
> 建议策略：**生产侧保留 CSV 落盘**（与当前 `factor_engine_runner` 一致）→ **每日/每批 ETL 转 Parquet** 写 COS + 更新 `watermark` / `manifest`。

---

## 0. 原则

1. **一期不改主存储类型**：`factor_value_files.artifact_type` 仍以 `batch_csv` / `daily_csv` 为权威；Parquet 作为**派生产物**或**扩展行**（见 §4）。  
2. **对外只读 Parquet**（+ 元数据 JSON），不强迫客户读 CSV。  
3. **幂等**：同一 `(universe, month, build_id)` 可重跑覆盖。  
4. **复现**：发布记录带 `code_git_sha` / `build_id` / `as_of_trade_date`。

---

## 0.1 月增 `batch_csv` 与日更 `daily_csv`（实现任务，与《腾讯云…》§5.3 一致）

- [ ] **月增完成后**：对受影响自然月做 **全月** Parquet 宽表重算/覆盖写（`month=YYYY-MM`）。  
- [ ] **日更完成后**：仅对 **当前月** 分区做 **尾部 patch**（主键 `stock_code + trade_date`；**已拍板：日更行覆盖** 同月同域同键下、由月增 `batch` 经合并后写入的对外宽表行；`watermark` 中写清此规则）。  
- [ ] **rebase 完成后**：对 `date_start~date_end` 覆盖到的**所有**相关 `month` 分区重算。  
- [ ] `watermark` 同时维护 `as_of_trade_date` 与（可选）`batch_production_max_date` / `daily_max_trade_date`。  
- [ ] 若日更因子集 ⊂ 月增全量集，在元数据中声明 **列缺失** 语义。  

详细产品说明见：  
`docs/腾讯云环境下因子+K线+训练标签 外网交付完整解决方案（适配LightGBM训练）.md` **§5.3**。

---

## 1. 目录与文件约定（COS，已收口）

- 分区：`factor/universe=.../month=YYYY-MM/part-NNN.parquet`（K 线、标签同理；文件内必含 `trade_date` 列用于过滤）。  
- 对外宽表列：`stock_code` + `trade_date` + 因子列 `F...` 或 `FACTOR_...` + `kline` 列 + `y_ret_1d`（与《腾讯云…修正版》一致）。  
- 内部长表真源：可选同 bucket 下 `raw_long/` 或仅保留在仓库内 CSV，由你取舍。

---

## 2. 依赖与工程准备

- [ ] **ETL 固定使用 Polars**（读 CSV、透视宽表、按月写 Parquet）；`requirements.txt` 增加并固定版本 `polars`。  
- [ ] **PyArrow**：随 Polars 作为 Parquet 读写依赖即可；一般不必在业务代码里单独 `import pyarrow`（除非你要极底层控制 row group）。  
- [ ] 本地与 CVM 环境均可 `import polars` 无报错。  
- [ ] **客户侧**：只需能读标准 Parquet（pandas / Polars / DuckDB 均可），**不要求**客户安装 Polars。  
- [ ] 若写 COS：集成 `cos-python-sdk-v5` 或 `qcloud_cos`，配置 **内网/密钥** 与 bucket 名（**不进仓库**）。

---

## 3. 新增模块（建议目录）

在仓库中新增**独立**目录，与 `factor_engine` 解耦，避免污染计算主路径：

- [ ] `src/factor_export_cos/`（名称可改）  
  - `etl_batch_csv_to_parquet.py`：读 `factor_value_files` + 磁盘 CSV → 宽表/长表 → 写 `month` 分区 Parquet。  
  - `meta_watermark.py`：写 `meta/watermark/{UNIVERSE}.json`。  
  - `meta_manifest.py`：写 `meta/manifest/{UNIVERSE}/{month}.json`（或按月单文件，与文档统一）。  
  - `config.py`：COS 前缀、单 part 行数上限、压缩 `zstd`。

---

## 4. 数据库（可选但推荐）

**方案 A（最小）**：不建新表，ETL 日志打本地文件 + `meta/update_log.parquet` 上 COS。

**方案 B（推荐，便于对账）**：新表 `factor_parquet_artifacts`（名称自定），字段示例：

- `id`, `universe`, `artifact_kind`（`wide_train` / `long_internal`）  
- `month`（`YYYY-MM`）  
- `cos_prefix` 或 `manifest_uri`  
- `build_id`, `as_of_trade_date`  
- `source_batch_ids`（text 或 jsonb，来自 `factor_value_files`）  
- `created_at`, `status`, `error_message`

- [ ] 出 DDL（PostgreSQL 兼容）与迁移说明（单人可手工执行一次）。

若暂不做 B：至少在 `watermark.json` 里带 `source_batch_id` 摘要。

---

## 5. 与 `factor_value_files` 的衔接

- [ ] ETL 入口参数：`config.ini` 路径、`universe`、可选 `month` 或 `date` 游标。  
- [ ] 只消费 `stage=production` 的 `batch_csv`（与文档一致；若你训练侧后来实现 v1 多段拼接，ETL 必须复用**同一**合并规则）。  
- [ ] `factor_engine_runner` 当前写 CSV 的相对路径入表逻辑**保持不变**。

---

## 6. 因子宽表列名

- [ ] 建立 **`factor_id` → 宽表列名** 映射表（可 JSON 放在 `meta/` 或 DB），规则固定（例如去非法字符、长度上限）。  
- [ ] 新增因子：映射追加；旧列不变，保证**历史宽表不破坏**（新因子从新 `month` 起有值即可）。

---

## 7. 日批调度

- [ ] CVM `cron` 或现有 `bin/*.sh` 增加一步：**因子 CSV 已落盘且 DB 已提交后** 再跑 ETL。  
- [ ] 失败告警：打日志 + 可选企业微信/邮件；**不**静默跳过 `watermark` 推进。

---

## 8. 控制面 API（轻量）

- [ ] `GET /latest?universe=ZZ500` → 返回 `as_of_trade_date`。  
- [ ] `POST /manifest` → 根据请求区间与因子列表，返回预签名 URL 列表（指向 Parquet 与/或 `manifest` JSON）。  
- [ ] 鉴权：token + 可选按 universe 白名单。

---

## 9. 客户 SDK（最小 3 接口）

- [ ] `get_latest(universe)`  
- [ ] `get_manifest(universe, start, end, factors, ...)`  
- [ ] `download_and_scan(...)`（或文档化让客户自写 Polars 并行读）

---

## 10. 验收清单

- [ ] 给定 `universe=ZZ500`、`month=某月`，Parquet 可读；`trade_date` 子集与 CSV 抽样本一致（抽样 5 日×20 股）。  
- [ ] `watermark.as_of_trade_date` 与 DB `MAX(date_end)` 对生产 batch 可解释一致。  
- [ ] 重跑 ETL 同一 `build_id` 结果一致或明确覆盖策略。  
- [ ] 单客户用文档示例可完成 LightGBM 读入训练（不要求零预处理，但**键对齐**）。

---

## 11. 回滚

- [ ] 关闭 ETL cron 与 API；COS 中旧数据保留；`factor_value_files` 与 CSV 不动即可恢复。  
- [ ] 不在一期删除任何 CSV 历史文件。

---

## 12. 后续二期（非必须）

- [ ] 将 `to_csv` 改为可选 `to_parquet` 双写（需全面回归回测/策略侧读路径）。  
- [ ] 宽表与长表全自动化一致性校验任务。

---

## 13. 参考文档

- `docs/腾讯云环境下因子+K线+训练标签 外网交付完整解决方案（适配LightGBM训练）.md`  
- `docs/因子值对外服务_架构与方案.md`  
- `docs/因子值增量方案_v1.md`  
- `docs/complete/db_conventions.md`

| 日期 | 说明 |
|------|------|
| 2026-04-27 | 初版：CSV 真源 + Parquet 外发 + COS 分区的改造任务清单 |
| 2026-04-27 | 技术选型收口：ETL 使用 Polars；客户侧仅依赖标准 Parquet |
| 2026-04-27 | 新增 §0.1：月增/日更 与月分区 Parquet 的配合（指向《腾讯云…》§5.3） |
| 2026-04-27 | 冲突规则定稿：同键 **日更覆盖月增** |
