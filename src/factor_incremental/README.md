# factor_incremental

`factor_incremental` 是因子值增量/重算的编排层，负责：

- 计算本次运行区间（增量或重算）；
- 处理 warmup（按交易日向前回看）；
- 生成批次标识（`batch_id`）；
- 标记是否重算（`is_rebase`）；
- 调用 `factor_engine_runner` 执行通用因子计算。

## 配置文件

- 默认读取：仓库根 `config.ini`（开发环境自动切 `config_dev.ini`）
- 核心项：
  - `factor_engine_config_file`
  - `mode`
  - `stage`
  - `warmup_trading_days`

## 运行示例

```bash
python src/factor_incremental/factor_incremental_runner.py --config config.ini --mode incremental --as-of-date 2026-04-30
```

```bash
python src/factor_incremental/factor_incremental_runner.py --config config.ini --mode rebase --as-of-date 2026-04-30 --batch-id rebase_2026Q2
```

## 参数说明

- `--mode`: `incremental` / `rebase`
- `--as-of-date`: 本批次截止日期（默认今天）
- `--stage`: 写入 `factor_value_files.stage`（默认 `candidate`）
- `--batch-id`: 可选，手工指定批次号
- `--warmup-trading-days`: 可选，覆盖配置中的 warmup 交易日

