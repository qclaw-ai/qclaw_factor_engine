# 因子工厂改造：Parquet 主存储方案 v1

> 目标：将当前 `csv` 主存储替换为 `parquet` 主存储，同时保留现有增量/发布语义，避免对训练与回测链路造成不可控回归。

**分步落地清单（按顺序执行）**：见 `docs/因子工厂_Parquet主存储改造_实施步骤.md`。

---

## 1. 本次改造结论（已按你的决策收口）

- 主存储切换为 `Parquet`，不再以 `CSV` 作为主产物。
- 年文件采用**长表**，文件名固定为 `{factor_id}-{year}.parquet`，放在 `by_universe/{universe}/{factor_id}/` 目录下。
- 日更文件采用**每个 universe 每天一份多因子文件**。
- `factor_value_files` 继续保留，并采用**单行覆盖**模式管理“年度单因子 Parquet”索引。
- 每日多因子 `factors.parquet` 暂不入库新表，先走“固定目录 + 轻量 manifest”方案（个人维护优先）。

---

## 2. 目标目录与文件命名

### 2.1 年度单因子长表（主存储）

- 路径：`factor_values_parquet/yearly/by_universe/{universe}/{factor_id}/{factor_id}-{year}.parquet`
- 文件示例：`factor_values_parquet/yearly/by_universe/ZZ500/FACTOR_MA_001/FACTOR_MA_001-2026.parquet`
- 表结构（长表）：
  - `trade_date` (date)
  - `stock_code` (varchar)
  - `factor_value` (double)

### 2.2 每日多因子文件（主存储）

- 路径：`factor_values_parquet/daily/by_universe/{universe}/{trade_date}/factors.parquet`
- 文件示例：`factor_values_parquet/daily/by_universe/ZZ500/2026-04-30/factors.parquet`
- 表结构（长表）：
  - `trade_date` (date)
  - `stock_code` (varchar)
  - `factor_id` (varchar)
  - `factor_value` (double)

### 2.3 对外 Parquet（保持）

- 继续保留既有导出链路（`factor_export_cos`）按月宽表输出，不影响客户读取契约。

---

## 3. `factor_value_files` 如何处理（核心）

## 3.1 现状约束

`factor_value_files.factor_id` 当前为 `NOT NULL` 且有外键指向 `factor_basic(factor_id)`。  
这意味着它天然适合“单因子单文件”，不适合“多因子单文件”。

## 3.2 处理原则（最终）

- **不破坏** `factor_value_files` 现有语义与历史数据。
- `factor_value_files` 仅登记“年度单因子 Parquet”。
- `factor_value_files` 采用单行覆盖：同一 `(factor_id, universe, artifact_type, year)` 始终只有一行。
- 每日多因子 `factors.parquet` 不入 `factor_value_files`，也暂不新建索引表，先通过 manifest 跟踪。

## 3.3 单行覆盖语义（推荐给个人开发）

新增字段（建议）：

- `year` int NOT NULL（用于与 `{factor_id}-{year}.parquet` 一一对应）
- `updated_at` timestamp NOT NULL DEFAULT `CURRENT_TIMESTAMP`
- `last_batch_id` varchar(128) NULL

唯一键建议：

- `UNIQUE (factor_id, universe, artifact_type, year)`

覆盖更新建议：

- daily 回补或重算命中同一主键时，执行 `UPDATE` 覆盖 `rel_path/date_start/date_end/updated_at/last_batch_id`。
- 尾年不完整时，`date_end` 保持最新可用交易日；文件名仍是整年命名。

### 3.4 `last_batch_id` 与已有 `batch_id` 的含义

- **`batch_id`（表上已有）**：一次因子任务/编排入口带的**批次号**（如 `inc_20260430`、`daily_20260430`）。在 `yearly_parquet` 单行覆盖模式下，每次成功写盘或合并更新该年索引行时，**应把 `batch_id` 更新为当前任务批次**，以便与日志、manifest 对齐。
- **`last_batch_id`（迁移新增，可选使用）**：语义上表示「**最后一次**把该 `(factor_id, universe, year)` 索引行对应文件写成功」时的批次号。  
  - **个人维护最简做法**：每次 `UPDATE` 年索引行时让 `last_batch_id = 当前 batch_id`（与 `batch_id` 同值即可），或**只维护 `batch_id`、不单独填 `last_batch_id`**（`last_batch_id` 留空），二选一即可，避免心智负担。  
  - **若将来要区分**：例如「首算批次」与「仅 daily 回补批次」不同，可约定 `batch_id` 保留首次全量登记、`last_batch_id` 每次覆盖都刷新——当前方案不强制，以你实际编排为准。

---

## 4. 数据库迁移建议（DDL 级别）

## 4.1 扩展 `factor_value_files.artifact_type`

新增枚举语义（若当前不是枚举，可直接按约定写字符串）：

- `yearly_parquet`
- （兼容期保留）`batch_csv` / `daily_csv`

## 4.2 保持 `factor_value_files` 的 `factor_id` 约束不动

- 不改 `NOT NULL`。
- 不改外键。
- 避免引入“伪因子ID”污染 `factor_basic`。

## 4.3 新增 `year` 列（建议）

- 用于将数据库主键语义与文件命名 `{factor_id}-{year}.parquet` 对齐。
- 让 upsert 条件简单、可读、可维护（个人开发下更重要）。

---

## 5. 计算与写入流程改造

## 5.0 计算入口：仍按「日期区间」，不要求按「年」调度

- **因子值计算**与现在一致：入口仍是 **`start_date`～`end_date`（及 warmup 下的更早起点）**，按**交易日序列**在内存里算，不是「只能按自然年跑一次」。
- **锚点仍可以是日**，例如 `2015-01-01`：编排层把业务区间右端对齐到交易日即可；`factor_engine_runner` 不需要改成「仅接受 year」。
- **落盘**：算完后，把本次**业务输出区间**（`publish_start_date`～`end_date`）内的行，按日历 **`year` 拆分写入**对应 `{factor_id}-{year}.parquet`；若一次任务跨年，则**写多个年文件**，并分别 upsert 多行 `yearly_parquet`（每年一行索引）。

## 5.1 `factor_engine_runner` 主写路径改造

- 当前：计算后写 `CSV` + upsert `factor_value_files(batch_csv|daily_csv)`。
- 改造后：
  - 写年度单因子 `parquet`（`{factor_id}-{year}.parquet`）。
  - upsert `factor_value_files(artifact_type=yearly_parquet)`。
  - 不再写主链路 `CSV`（可保留临时开关用于灰度期）。

## 5.2 daily runner 改造

- 当前：单因子单文件 `daily_csv`。
- 改造后：
  - 单次任务先产出日更长表全集（多因子）。
  - 写 `factors.parquet`（每个 universe 每天一份）。
  - 写当日 `manifest.json`（记录路径、行数、因子数、批次号）。

## 5.3 daily 回补年度文件

- 输入：当日 `factors.parquet`。
- 处理：
  - 按 `factor_id` 切分当日数据。
  - 定位对应 `{factor_id}-{year}.parquet`。
  - 以 `(trade_date, stock_code)` 为主键执行覆盖写（同键 daily 覆盖原值）。
- 输出：
  - 更新对应年度单因子文件。
  - 记录补写统计日志（命中因子数、覆盖行数、失败因子数）。

## 5.4 `factor_engine_runner`：跳过已计算（幂等）如何改

**历史（引擎已不再使用）**：非 daily 模式曾用 `batch_csv` 索引精确匹配 `(date_start, date_end)` 做整因子跳过。

**当前（`yearly_parquet` 主链路）**：

- 对本次任务业务区间 `[pub_start, pub_end]` 所覆盖的**每一个日历年 `Y`**，分别查 `factor_value_files`：`artifact_type = 'yearly_parquet'`、`year = Y`、`universe/factor_id` 与当前任务一致（`stage` 与任务约定一致，如仅看 `production` 或含 `candidate`）。
- **跳过条件（推荐）**：该年索引行存在，且索引区间 **同时** 盖住本任务在 `Y` 年内的需求闭区间：
  - 左、右端建议按 **`stock_daily` 实际交易日** 对齐后再与索引 `date_start/date_end` 比较（因子 parquet 的 `trade_date` 与行情一致；若用纯日历 `Y-01-01` 而当年首个交易日更晚，会误判定「未覆盖」导致同任务重复计算）。
  - 日历裁剪：`cal_start = max(pub_start, Y-01-01)`，`cal_end = min(pub_end, Y-12-31)`；再取 `need_start = MIN(trade_date)`、`need_end = MAX(trade_date)`（`stock_daily`，`trade_date BETWEEN cal_start AND cal_end`）。
  - 要求 **`date_start <= need_start` 且 `date_end >= need_end`**。
  - **尾年不完整**：同上，双侧都要满足。
- **跨年任务**：对每个 `Y` 独立判断；**任一年**未满足上述覆盖则该因子**仍参与计算**（或实现为只算缺年，属优化项）。
- **迁移期**：导出/回测若仍读历史 `batch_csv` 行，可在读取层单独做回退；**引擎批量跳过**已按 `yearly_parquet` 索引与交易日边界判定，不再依赖 `skip_fallback_batch_csv` 等已移除 ini 项。

**daily**：

- 当前实现：日更仅落 `factors.parquet` + `manifest.json`（bundle 路径见 `factor_engine_runner`）；**幂等跳过**在任务入口检查**目标 `factors.parquet` 是否已存在**，由配置项 **`skip_if_artifact_record_exists`** 控制（默认 true）。

**配置命名**：`skip_if_artifact_record_exists`；Python 覆写参数 `skip_if_artifact_record_exists_override`。

---

## 6. 读取层改造（训练/回测）

- 训练与回测读取 `factor_value_files` 时优先取 `yearly_parquet`。
- 训练窗口拼接规则：
  - 先按年文件覆盖训练区间。
  - 如需当日补丁，按 daily 目录与 manifest 叠加读取。
- 读取层对历史 `batch_csv`/`daily_csv` 路径的兼容可按导出/回测模块各自策略保留；引擎主写已不再依赖 CSV 开关。

---

## 6.1 watermark 与 manifest（最终）

- watermark：**需要**，每个 universe 一份 JSON（最少包含 `as_of_trade_date`、`updated_at`）。
- manifest：**建议保留轻量版**，每次 daily/回补任务一份 JSON（记录输入、输出、覆盖行数、批次号）。
- 原则：watermark 单调前进，不因补历史而回退。

---

## 7. 迁移步骤（建议顺序）

1. `factor_value_files` 增加 `year/updated_at/last_batch_id` 与唯一键（单行覆盖模式）。
2. `factor_engine_runner` 增加 `yearly_parquet` 写入与 upsert（先双写灰度）。
3. `daily_factor_values_runner` 改为每日多因子 `factors.parquet` + 轻量 manifest。
4. 增加“daily 回补 yearly”任务（可在 daily 后串行执行）。
5. `factor_export_runner` 与训练侧切换读取 parquet 主链路。
6. 验证通过后关闭 CSV 主写（保留历史文件，不删除）。

---

## 8. 验收清单

- 任意因子在任意年份只存在一个主文件：`{factor_id}-{year}.parquet`。
- daily 文件满足“每 universe 每天一份”且可被回补任务消费。
- 同键冲突遵循 `daily 覆盖 yearly`。
- 训练区间读取不再依赖 `CSV`，且回测结果与基线偏差在可接受范围内。
- `factor_value_files` 的单行覆盖字段（`date_start/date_end/updated_at/last_batch_id`）可追溯当前状态。
- daily 侧可通过 manifest 追溯输入/输出与覆盖统计。

---

## 9. 风险与规避

- 风险：Parquet 覆盖写会触发整文件重写，年文件可能偏大。  
  规避：按 `factor_id + year` 粒度已经较细；后续可追加“季度分片”作为 v2。

- 风险：daily 回补与年重算并发写同一文件。  
  规避：同一 `factor_id-year` 加文件锁或任务串行化。

- 风险：旧脚本仍查 `batch_csv/daily_csv`。  
  规避：改造期双写 + 读取优先级开关，逐模块切换。

---

## 10. 修订记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-04-30 | v1 | 按“Parquet 主存储 + 年度单因子长表 + 每日多因子单文件”决策初版落地方案 |
| 2026-05-04 | v1.1 | 补充：计算入口仍为日期区间；`yearly_parquet` 下跳过已计算规则；`last_batch_id` 与 `batch_id` 说明 |
| 2026-05-04 | v1.2 | 增加指向《实施步骤》文档的入口链接 |

