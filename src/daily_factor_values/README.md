# 日更因子值（`daily_factor_values_runner`）

## 作用

- 按 **因子值所属交易日 `T`**（`--trade-date`）计算各 `factor_basic.is_valid=TRUE` 且 `factor_docs` 有定义的因子。
- 写出长表 CSV：`trade_date, stock_code, factor_value`。
- 路径：`factor_values/daily/{factor_id}/{T}.csv`（相对仓库根）。
- 仅更新 **`factor_files.factor_values_path_daily`**，**不修改** `factor_values_path`（评估线）。

## 运行示例

在仓库根 `qclaw_factor_engine` 下：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config src/daily_factor_values/config_dev.ini ^
  --trade-date 2025-12-31
```

### `factor_values_path_daily` 为 NULL 的常见原因

1. **默认 `scope=valid_only`**：只跑 `factor_basic.is_valid=TRUE`。未过入库阈值的因子（仍有年度 `factor_values_path`）**不会进日更**，DB 中日更列为空属预期。  
   - 需要给「库里有记录、文档也有公式」的**全部因子**写日更路径时：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config src/daily_factor_values/config_dev.ini ^
  --trade-date 2025-12-31 ^
  --scope all_in_basic
```

2. **某日在截面上无行**：多为 **`lookback_days` 偏短**，长窗口因子在 T 日尚未形成有效值，可增大 `[daily] lookback_days`。

3. **终端里 numpy 的 RuntimeWarning**：多为 `quantile`/缺失值引起，一般**不等于**写库失败；以日志里是否出现「已写出…并更新 factor_values_path_daily」为准。

联调只跑少量因子：

```bash
python src/daily_factor_values/daily_factor_values_runner.py ^
  --config src/daily_factor_values/config_dev.ini ^
  --trade-date 2025-12-31 ^
  --factor-ids JQ_ALPHA_000,JQ_ALPHA_001
```

## 依赖

- `stock_daily` 在 `[T-lookback_days, T]` 内有数据。
- 表 `factor_files` 已存在列 **`factor_values_path_daily`**。
