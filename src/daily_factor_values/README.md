# 日更因子值（`daily_factor_values_runner`）

## 作用

- 按 **因子值所属交易日 `T`**（`--trade-date`，**未传则默认当天** `YYYY-MM-DD`）计算各 `factor_basic.is_valid=TRUE` 且 `factor_docs` 有定义的因子。
- **`T` 若不在已拉取的行情交易日中**（周末、节假日、或数据尚未同步到该日）：自动对齐为 **不大于 `T` 的最近交易日**；若 `T` 早于行情最早日则对齐为 **最早交易日**（日志会 WARNING/INFO）。
- 支持按 **`--universe`** 生成分域日更（如 `ALL`、`HS300`，历史 `ALL_A` 自动归一到 `ALL`）。
- 计算入口仍为 `factor_engine_runner` 的 **`daily_csv_mode=True`**（历史命名；**主产物为 Parquet bundle**，不再写逐因子 CSV；与 incremental 的 warmup 对齐）。

## 落盘（阶段 2）

- **多因子 Parquet（默认开启）**：任务结束后合并写  
  `factor_values_parquet/daily/by_universe/{UNIVERSE}/{T}/factors.parquet`  
  长表列：`trade_date, stock_code, factor_id, factor_value`（`zstd`）。
- **manifest**：同目录 `manifest.json`（`trade_date, universe, batch_id, factor_count, row_count, parquet_rel_path, generated_at` 等）。
- **幂等跳过**：`[factor_engine].skip_if_artifact_record_exists=true`（默认）且 `daily_parquet_bundle_enabled=true` 时，若入口已检测到目标目录下存在 **`factors.parquet`**，则**全日更任务直接跳过**（不再拉行情、不算子因子）。

配置项写在 **`factor_engine` 所用配置文件**（如根目录 `config.ini` / `config_dev.ini`，由 `[factor_incremental].factor_engine_config_file` 指定）的 **`[factor_engine]`** 段：

- `daily_parquet_bundle_enabled`（默认 `true`）
- `skip_if_artifact_record_exists`（默认 `true`）

- 开发阶段不做兼容回退：不写 `factor_files.factor_values_path_daily`，不修改 `factor_values_path`（评估线）。

## 阶段 3：日更 bundle → 年度 Parquet（`daily_parquet_merge_to_yearly_runner`）

- **独立脚本**：只读已有 `factors.parquet`（+ 可选 `manifest.json` 取 `batch_id`），按因子、日历年调用与全量引擎相同的合并与 `yearly_parquet` upsert。
- **恢复策略**：日更已成功、仅回补/DB 失败时，**只重跑本脚本**，不必重跑 `daily_factor_values_runner`。
- **配置**：`--config` 仍为根配置（`[database]`）；`stage`、`is_rebase` 从 `[factor_incremental].factor_engine_config_file` → `[factor_engine]` 读取（与 `daily_factor_values_runner` 一致）。`--universe` 默认 `[daily].universe`。

单交易日：

```bash
python src/daily_factor_values/daily_parquet_merge_to_yearly_runner.py ^
  --config config.ini ^
  --universe ZZ500 ^
  --trade-date 2018-12-28
```

区间内扫描已有 bundle（闭区间，仅处理目录下已有 `factors.parquet` 的日期）：

```bash
python src/daily_factor_values/daily_parquet_merge_to_yearly_runner.py ^
  --config config.ini ^
  --universe ZZ500 ^
  --from-date 2018-12-01 ^
  --to-date 2018-12-28
```

只预览、不写盘不改库：

```bash
python src/daily_factor_values/daily_parquet_merge_to_yearly_runner.py ^
  --config config.ini ^
  --universe ZZ500 ^
  --trade-date 2018-12-28 ^
  --dry-run
```

### 边界与缺口（脚本行为说明）

- **当年还没有 yearly 文件**：首次合并会新建文件并 upsert，正常。
- **年文件里「最后交易日」比本次目录日早很多**：合并只会追加**当前** `factors.parquet` 里的行，**不会**自动发明中间缺失的交易日；中间空洞要靠补跑历史日更 + 本脚本，或跑 **batch 全量/区间重算**。
- **告警 / 熔断**：默认若已有 yearly 的 `max(trade_date)` 早于本次 bundle 所属日超过 **14 个自然日**，打 WARNING（可调 `--gap-warning-calendar-days`，`0` 关闭检测）。若你认为「看见大洞就不应再硬补」，加 **`--halt-on-calendar-gap`**：同一判定触发时 **立即退出码 2**、不写盘不改库，便于 cron 报警后再补中间日更或做 batch。

## 运行示例

在仓库根 `qclaw_factor_engine` 下：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config config.ini ^
  --universe ALL
```

（省略 `--trade-date` 时使用运行当日的日期。指定某日可显式传入，例如 `--trade-date 2025-12-31`。）

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config config.ini ^
  --trade-date 2025-12-31 ^
  --universe ALL
```

### 日更产物或因子集合不符合预期

1. **默认 `scope=valid_only`**：只跑 `factor_basic.is_valid=TRUE`。未过入库阈值的因子**不会进日更**。  
   - 需要给「库里有记录、文档也有公式」的**全部因子**参与日更计算时：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config config.ini ^
  --trade-date 2025-12-31 ^
  --universe ALL ^
  --scope all_in_basic
```

2. **某日在截面上无行**：多为 **[factor_incremental].warmup_trading_days（或 `--warmup-trading-days`）偏小**，或 **`stock_daily` 历史太短**（先检查 `daily_stock_and_calendar_sync` 的 `--lookback-days`）。

3. **终端里 numpy 的 RuntimeWarning**：多为 `quantile`/缺失值引起，一般**不等于**写盘失败；以日志里是否出现 **「daily parquet bundle 已写入」** 及 `factors.parquet` 路径为准。

联调只跑少量因子：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config config.ini ^
  --trade-date 2025-12-31 ^
  --universe HS300 ^
  --factor-ids JQ_ALPHA_000,JQ_ALPHA_001
```

## 依赖

- `stock_daily` 覆盖引擎所需区间：**pipeline** 侧用自然日回填（例如 `daily_stock_and_calendar_sync --lookback-days`）；因子侧 warmup 见 `[factor_incremental].warmup_trading_days`。
- 日更 bundle 当前**仅写磁盘** `factors.parquet` + `manifest`；批量/回补后的 **`yearly_parquet`** 仍通过 `factor_value_files` 登记（见 `factor_engine_runner`）。
