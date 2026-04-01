# 日更因子值（`daily_factor_values_runner`）

## 作用

- 按 **因子值所属交易日 `T`**（`--trade-date`，**未传则默认当天** `YYYY-MM-DD`）计算各 `factor_basic.is_valid=TRUE` 且 `factor_docs` 有定义的因子。
- **`T` 若不在已拉取的行情交易日中**（周末、节假日、或数据尚未同步到该日）：自动对齐为 **不大于 `T` 的最近交易日**；若 `T` 早于行情最早日则对齐为 **最早交易日**（日志会 WARNING/INFO）。
- 支持按 **`--universe`** 生成分域日更（如 `ALL`、`HS300`，历史 `ALL_A` 自动归一到 `ALL`）。
- 写出长表 CSV：`trade_date, stock_code, factor_value`。
- 路径：`factor_values/daily/by_universe/{UNIVERSE}/{T}/{factor_id}.csv`（相对仓库根）。
- 主登记：更新 **`factor_value_files`**（`artifact_type=daily_csv`，含 `universe` 维度）。
- 开发阶段不做兼容回退：不写 `factor_files.factor_values_path_daily`，不修改 `factor_values_path`（评估线）。

## 运行示例

在仓库根 `qclaw_factor_engine` 下：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config src/daily_factor_values/config_dev.ini ^
  --universe ALL
```

（省略 `--trade-date` 时使用运行当日的日期。指定某日可显式传入，例如 `--trade-date 2025-12-31`。）

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config src/daily_factor_values/config_dev.ini ^
  --trade-date 2025-12-31 ^
  --universe ALL
```

### `factor_value_files(daily_csv)` 缺记录的常见原因

1. **默认 `scope=valid_only`**：只跑 `factor_basic.is_valid=TRUE`。未过入库阈值的因子（仍有年度 `factor_values_path`）**不会进日更**，DB 中日更列为空属预期。  
   - 需要给「库里有记录、文档也有公式」的**全部因子**写日更路径时：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config src/daily_factor_values/config_dev.ini ^
  --trade-date 2025-12-31 ^
  --universe ALL ^
  --scope all_in_basic
```

2. **某日在截面上无行**：多为 **`lookback_days` 偏短**，长窗口因子在 T 日尚未形成有效值，可增大 `[daily] lookback_days`。

3. **终端里 numpy 的 RuntimeWarning**：多为 `quantile`/缺失值引起，一般**不等于**写库失败；以日志里是否出现「已写出…并更新 factor_value_files(daily_csv)」为准。

联调只跑少量因子：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config src/daily_factor_values/config_dev.ini ^
  --trade-date 2025-12-31 ^
  --universe HS300 ^
  --factor-ids JQ_ALPHA_000,JQ_ALPHA_001
```

## 依赖

- `stock_daily` 在 `[T-lookback_days, T]` 内有数据。
- 表 `factor_value_files` 可写，且支持 `artifact_type='daily_csv'`、`universe` 维度与 `rel_path` 更新。
