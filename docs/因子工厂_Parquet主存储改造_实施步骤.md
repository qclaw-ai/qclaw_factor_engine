# 因子工厂 Parquet 主存储：实施步骤（执行清单）

> 设计依据：`docs/因子工厂_Parquet主存储改造方案_v1.md`  
> 用法：按步骤顺序执行；每步完成后再进入下一步；可在本文件勾选进度。

---

## 前置约定（开始前确认）

- [ ] 已阅读并认同方案 v1（目录、`yearly_parquet` 单行覆盖、尾年 `date_start/date_end`、跳过规则、watermark/manifest）。
- [ ] 目标环境：PostgreSQL；Python 依赖后续写 Parquet 需 `polars` 或 `pyarrow`（与现有 `factor_export_cos` 对齐则优先 **Polars**）。
- [ ] 备份：执行 migration 前备份 DB 或至少在测试库跑通一遍。

---

## 阶段 0：数据库与基线 schema

| 序号 | 任务 | 产出/验收 |
|------|------|-----------|
| 0.1 | 在**测试库**执行 `sql/migrations/008_factor_value_files_yearly_parquet_single_row.sql` | 无报错；`\d factor_value_files` 可见 `year`、`updated_at`、`last_batch_id` 及新 CHECK/索引 |
| 0.2 | 确认 `sql/schema_mvp.sql` 与 migration 语义一致（新库初始化与迁移对齐） | 代码审查通过 |
| 0.3 | （生产）择窗执行同一 migration | 与 0.1 一致 |

---

## 阶段 1：`factor_engine_runner` — 年 Parquet 写入 + 索引 upsert

| 序号 | 任务 | 产出/验收 |
|------|------|-----------|
| 1.1 | 抽取或新增「写 Parquet 长表」工具函数（列：`trade_date, stock_code, factor_value`），路径：`factor_values_parquet/yearly/by_universe/{universe}/{factor_id}/{factor_id}-{year}.parquet` | 已实现 `_merge_write_yearly_parquet_long`；单测 `tests/test_factor_yearly_parquet_helpers.py` |
| 1.2 | 在 `run_factor_engine` 落盘处：将 `df_out` 按 **`publish_start_date`～`end_date` 覆盖到的每个 `year`** 切片；对每年合并进对应年文件（读旧 + concat + 按 `trade_date,stock_code` 去重保留新）后写回 | 已实现（非 daily、非空 `df_out`） |
| 1.3 | 实现 `_upsert_factor_value_file_yearly_parquet`（或等价）：「先 UPDATE 再 INSERT」；唯一键 `(factor_id, universe, artifact_type, year)`；更新 `rel_path, date_start, date_end, updated_at, batch_id, last_batch_id` | 已实现 |
| 1.4 | **跳过逻辑**：非 `daily_csv_mode` 时，按方案 **§5.4** 判断 `yearly_parquet` 的 `date_start`/`date_end` 是否双侧盖住 publish 各年需求区间；由 `skip_if_artifact_record_exists` 控制 | 已实现 |
| 1.5 | 配置项：`yearly_parquet_enabled`（默认 true）、`skip_if_artifact_record_exists`（默认 true）、`daily_parquet_bundle_enabled`（默认 true） | 已写入各 `config*.ini` / `src/factor_engine/config_sample.ini` |

**阶段 1 完成标准**：命令行跑一段区间后，磁盘有年 parquet + `factor_value_files` 中 `yearly_parquet` 行正确；跳过行为符合预期。

---

## 阶段 2：日更 `daily_factor_values_runner` — 多因子单文件 + manifest

| 序号 | 任务 | 产出/验收 |
|------|------|-----------|
| 2.1 | 调整日更产物：写 `factor_values_parquet/daily/by_universe/{universe}/{trade_date}/factors.parquet`（列含 `factor_id`） | 已在 `factor_engine_runner`（`daily_csv_mode`）任务末合并写出 |
| 2.2 | 同目录写 `manifest.json`（字段含 `trade_date, universe, batch_id, factor_count, row_count, parquet_rel_path, generated_at`） | 同上 `_write_daily_parquet_bundle_and_manifest` |
| 2.3 | 日更仅落 `factors.parquet` + `manifest`；不再写逐因子 CSV、不再依赖 `daily_csv_fallback` 配置项 | 与当前代码一致 |
| 2.4 | 日更跳过：`skip_if_artifact_record_exists=true` 且 `daily_parquet_bundle_enabled=true` 时，入口若已存在目标 `factors.parquet` 则全日更跳过 | 已实现 |

**阶段 2 完成标准**：日更任务跑一天，目录与 manifest 符合约定。

---

## 阶段 3：daily → yearly 回补任务

| 序号 | 任务 | 产出/验收 |
|------|------|-----------|
| 3.1 | 脚本入口：`src/daily_factor_values/daily_parquet_merge_to_yearly_runner.py`；读 `factors.parquet`，按 `factor_id`、日历年分组，调用 `_merge_write_yearly_parquet_long`（同键覆盖） | 年文件包含新交易日 |
| 3.2 | 每组更新后 `_upsert_factor_value_file_yearly_parquet`（刷新 `rel_path`、`date_end`、`updated_at`、`batch_id`） | DB 与文件一致 |
| 3.3 | 调度顺序：日更计算 → 写 daily parquet/manifest → **单独**跑回补脚本（失败可只重跑回补；见 `src/daily_factor_values/README.md`） | 文档化顺序 |

**阶段 3 完成标准**：跑完一天后，年 parquet 与索引 `date_end` 推进；无并发写坏文件（必要时单进程锁）。

---

## 阶段 4：导出与下游读取

| 序号 | 任务 | 产出/验收 |
|------|------|-----------|
| 4.1 | `factor_export_runner.py`：新增 `_fetch_yearly_parquet_sources`、`_read_factor_parquet`；与月有交集的因子优先读 `yearly_parquet`，否则回退 `batch_csv`；`daily_csv` 仍为 `source_priority=2` 覆盖 | 月导出宽表含 yearly 数据 |
| 4.2 | `common/factor_value_files_batch.py`：`is_parquet_factor_rel_path`、`load_yearly_parquet_rel_paths`（`(factor_id,year)->rel_path`） | 策略/工具可读 yearly 路径 |
| 4.3 | manifest / watermark：增加 `yearly_source_*`、`conflict_policy=daily_over_yearly_over_batch_csv`；watermark 含 `yearly_source_rel_paths` | 与现有 `meta/` 兼容扩展 |

**阶段 4 完成标准**：`factor_export_runner` 能从 `yearly_parquet` 拉出月分区宽表。

---

## 阶段 5：关闭 CSV 主写与清理开关

| 序号 | 任务 | 产出/验收 |
|------|------|-----------|
| 5.1 | 自 ini 移除已无代码消费的 `write_csv_fallback`、`skip_fallback_batch_csv`、`daily_csv_fallback`；批量/日更主链路仅 Parquet | 配置与代码一致 |
| 5.2 | 更新 `src/factor_engine/README.md`、`src/daily_factor_values/README.md`、`docs/因子值生成使用说明.md`、仓库根同名说明、`docs/config_keys_matrix.md` | 文档与 Parquet 行为一致 |
| 5.3 | （可选）`scripts/bootstrap_factor_export_history.py` 注释与导出语义对齐 | 减少历史 CSV 误导 |

**阶段 5 完成标准**：默认配置下批量写 `yearly_parquet`、日更写 `factors.parquet`+manifest；幂等由 `skip_if_artifact_record_exists` 控制；库内旧 `batch_csv`/`daily_csv` 行可仍存在但不再是主写路径。

---

## 阶段 6：回归与发布

| 序号 | 任务 | 产出/验收 |
|------|------|-----------|
| 6.1 | 补/改单元测试：年切片、跳过规则、upsert 键 | CI 通过 |
| 6.2 | 选 1 个 universe + 少量因子 + 短区间做端到端：engine → daily → 回补 → export → validate | 与 `scripts/validate_factor_export.py` 或等价校验通过 |
| 6.3 | 生产发布 checklist（migration、配置、回滚：恢复写 CSV + 读旧 artifact_type） | 一页纸即可 |

---

## 建议执行顺序（总览）

```
0 DB migration
  → 1 factor_engine_runner（年 parquet + yearly 索引 + 跳过）
  → 2 daily（多因子 parquet + manifest）
  → 3 daily→yearly 回补
  → 4 factor_export + 公共读取
  → 5 关 CSV 主写
  → 6 回归与发布
```

---

## 修订记录

| 日期 | 说明 |
|------|------|
| 2026-05-04 | 初版：按方案 v1 拆阶段实施清单 |
| 2026-05-04 | 阶段 1 已在 `factor_engine_runner` 落地（见仓库该日提交） |
| 2026-05-04 | 阶段 5：默认关闭 `write_csv_fallback`；更新 `README` / `因子值生成使用说明` / `bootstrap` 注释 |
| 2026-05-04 | 文档同步：移除 ini 中已删 CSV 开关描述；明确幂等跳过对 Parquet 语义；配置键为 `skip_if_artifact_record_exists` |
