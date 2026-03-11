## 模块：backtest_io（回测结果落地）

> 职责：调用 `backtest_core` 获得一批因子的回测结果，将结果写成 JSON 文件，并插入数据库表 `factor_backtest`（必要时补齐 `factor_basic` 记录）。

---

### 1. 配置说明：`backtest_io/config_*.ini`

#### 1.1 `[database]`

用于写入 `factor_basic` / `factor_backtest`：

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = postgres
db_password = your_password_here
db_name = qclaw_factor_engine
```

#### 1.2 `[paths]`

```ini
[paths]
; 回测结果 JSON 输出目录（相对项目根）
backtest_results_dir = backtest_results
```

---

### 2. 数据流与依赖

输入依赖：

- 来自 `factor_engine` 的因子结果 CSV：
  - 目录：`factor_engine/output`
  - 文件名：`<factor_id>_<start_date>_<end_date>.csv`
  - 列：`trade_date, stock_code, factor_value`
- 来自 `backtest_core` 的计算逻辑：
  - `run_backtest(config_file)` 返回 `List[BacktestResult]`：
    - `factor_id`
    - `backtest_period`
    - `horizon`
    - `ic_value`
    - `ic_ir`
    - `sharpe_ratio`
    - `max_drawdown`
    - `turnover`
- 来自 `factor_docs` 的因子元数据：
  - `FactorDefinition`（用于填充 `factor_basic` 与 JSON 中的名称、类型等）。

输出：

- 文件系统：
  - `backtest_results/<factor_id>_backtest.json`
- 数据库：
  - `factor_basic`：若不存在该 `factor_id`，插入一条最小记录；
  - `factor_backtest`：为每次回测插入一行记录。

---

### 3. JSON 结构与 DB 写入逻辑

#### 3.1 JSON 文件结构

路径示例：`backtest_results/FACTOR_DEMO_0001_backtest.json`

```json
{
  "factor_id": "FACTOR_DEMO_0001",
  "factor_name": "20日动量因子示例",
  "factor_type": "动量",
  "test_universe": "HS300",
  "trading_cycle": "日线",
  "source_url": "https://example.com/factor/demo_0001",
  "backtest_period": "2024-01-02 至 2024-03-31",
  "horizon": "5d",
  "key_metrics": {
    "ic_value": -0.1515,
    "ic_ir": -0.1509,
    "sharpe_ratio": null,
    "max_drawdown": null,
    "turnover": null
  },
  "pass_standard": null,
  "backtest_time": "2026-03-11T18:50:00.000000"
}
```

- `factor_name` / `factor_type` / `test_universe` / `trading_cycle` / `source_url`：
  - 来自 `factor_docs` 中的 `FactorDefinition`；
  - 若未找到对应因子定义，则只保证 `factor_id`，其余为 `null` / 缺省。
- `pass_standard`：
  - 由 `selection_and_store` 根据阈值判断后再更新，这里先写 `null`。

#### 3.2 DB 写入逻辑

1. **保证 `factor_basic` 存在记录**
   - 对于每个 `factor_id`：
     - 若 `factor_docs` 有对应定义，则使用其信息；
     - 否则仅用 `factor_id` 作为名称。
   - 插入 SQL（简化示意）：

```sql
INSERT INTO factor_basic (
  factor_id, factor_name, factor_type,
  test_universe, trading_cycle, source_url
) VALUES (...)
ON CONFLICT (factor_id) DO NOTHING;
```

2. **插入 `factor_backtest` 记录**

```sql
INSERT INTO factor_backtest (
  factor_id, backtest_period, horizon,
  ic_value, ic_ir, sharpe_ratio, max_drawdown, turnover,
  pass_standard, comment
) VALUES (...);
```

- `pass_standard`：先写 `NULL`，由后续 `selection_and_store` 更新。
- `comment`：当前留空，未来可用于标记 `initial/recheck/reactivate` 等类型。

---

### 4. 运行方式

在项目根目录执行：

```bash
python backtest_io/backtest_io_runner.py
```

运行步骤：

1. 读取 `backtest_io/config_*.ini` 获取 DB 和 `backtest_results_dir`；
2. 调用 `factor_docs.load_all_factors()` 预加载因子元数据；
3. 调用 `backtest_core.run_backtest("backtest_core/config.ini")` 获取所有 `BacktestResult`；
4. 对每个结果：
   - 写 JSON 文件；
   - upsert 一条 `factor_basic`；
   - 插入一条 `factor_backtest`。

日志会输出每个因子的处理情况及总数，方便审计与排错。

---

### 5. 与后续模块的衔接

- `selection_and_store`：
  - 将从 `factor_backtest` 与 `factor_threshold_config` 中读取最新回测结果与阈值；
  - 根据指标是否满足条件更新：
    - `factor_backtest.pass_standard`
    - `factor_basic.is_valid` 等字段；
    - 同时维护 `factor_files.backtest_json_path` / `log_path`。

当前阶段 `backtest_io` 不做任何“好坏判断”，**只负责把回测结果完整、规范地落到文件和 DB**，为后续决策模块提供统一数据源。

