## 模块：backtest_core（单因子回测核心）

> 职责：基于因子值时间序列和未来收益，计算单因子的 IC / IC_IR、分层多空收益、夏普率、最大回撤和换手率等核心指标。

---

### 1. 配置说明：`backtest_core/config_*.ini`

#### 1.1 `[database]`

与其他模块保持一致，用于读取 `stock_daily` 收盘价：

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = postgres
db_password = your_password_here
db_name = qclaw_factor_engine
```

#### 1.2 `[backtest]`

```ini
[backtest]
; 回测 horizon（单位：交易日）
horizon = 5

; 分层数量（例如 10 表示 10 分位分组）
n_quantiles = 10

; 因子 CSV 根目录（将扫描该目录下所有 *.csv）
factor_output_dir = factor_engine/output

; 可选：仅回测这些因子ID（逗号分隔）；留空则对目录下所有 CSV 扫描
factor_ids =
```

- `horizon`：
  - 定义未来收益的持有期天数，MVP 固定为 5；
  - 未来收益定义为：`ret_h = ln(close_{t+h} / close_t)`。
- `n_quantiles`：
  - 每日按因子值分成的分组数量（默认 10 分位）；
  - 多空组合 = 最高组多头 - 最低组空头。
- `factor_output_dir`：
  - `factor_engine` 输出因子 CSV 的目录；
  - `backtest_core_runner` 会扫描该目录下的所有 `*.csv` 文件。
- `factor_ids`：
  - 若留空 → 对 `factor_output_dir` 下所有因子 CSV 逐个回测；
  - 若填写 → 仅回测这些因子 ID，对应规则为：**文件名以 `<factor_id>_` 开头**。

---

### 2. 计算流程概览

入口脚本：`backtest_core_runner.py`，主要步骤：

1. 读取 `config_*.ini`。
2. 扫描 `factor_output_dir` 下所有 `*.csv` 文件，按文件名前缀识别 `factor_id`：
   - 例如 `FACTOR_DEMO_0001_2024-01-01_2024-03-31.csv` → `factor_id = FACTOR_DEMO_0001`。
3. 对每个需要回测的因子：
   1. 加载 CSV，得到：
      - MultiIndex：`(trade_date, stock_code)`
      - 列：`factor_value`
   2. 从 `stock_daily` 读取相同时间区间内的 `close` 价格，计算未来收益：
      - `ret_h = ln(close_{t+h} / close_t)`。
   3. **IC / IC_IR：**
      - 对每个交易日截面，计算秩相关 IC（Spearman）；
      - `IC` 为日度 IC 的均值；
      - `IC_IR = IC_mean / IC_std`。
   4. **分层多空收益与换手率：**
      - 每日按因子值分成 `n_quantiles` 组；
      - 计算各组平均未来收益；
      - 多空日收益 `ls_ret = top_quantile_ret - bottom_quantile_ret`；
      - 简化版换手率：
        - 记录多头组合（最高分位）持仓；
        - `turnover_t = 1 - |L_t ∩ L_{t-1}| / |L_t|`；
        - 取时间平均作为 `turnover` 指标。
   5. **夏普与最大回撤：**
      - 基于多空日收益 `ls_ret`：
        - 夏普：`Sharpe = mean(ls_ret)/std(ls_ret) * sqrt(252)`；
        - 最大回撤：对 `(1 + ls_ret)` 的累积曲线计算 peak-to-trough 最大跌幅。
4. 在日志中输出每个因子的汇总指标，并返回 `BacktestResult` 列表（当前仅打印，不写 DB）。

---

### 3. 与 `factor_engine` 的对接约定

- `factor_engine` 输出的每个 CSV 文件必须满足：
  - 文件名形如：`<factor_id>_<start_date>_<end_date>.csv`；
  - 列包含：`trade_date, stock_code, factor_value`。
- `backtest_core_runner`：
  - 只依赖因子 CSV 与 `stock_daily`，**不关心 DSL 或 md**；
  - 可对任意来源的因子序列（只要格式一致）执行相同回测逻辑。

---

### 4. 运行方式

在项目根目录执行：

```bash
python backtest_core/backtest_core_runner.py
```

日志中会输出类似：

```text
回测结果 - factor_id=FACTOR_DEMO_0001, IC=-0.1515, IC_IR=-0.1509,
Sharpe=..., MaxDD=..., Turnover=...
本次共完成 1 个因子的回测
```

> 说明：当前示例数据仅有 2 只股票、时间区间较短，各项指标只用于验证流程正确性，本身并无统计意义。  
> 真正评估因子效果时，应在更长时间区间和更大股票池上运行。

