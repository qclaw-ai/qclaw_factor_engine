# 因子月度监控模块（factor_monitor）

## 1. 模块目的

`factor_monitor` 用于每月输出「因子健康报告」，核心回答：

- 这个月因子是否仍有效（IC/IC_IR/覆盖率）
- DB 自算结果与生产 `daily_csv` 是否一致（抽检对账）

当前定位：**只读计算 + 文件落盘**，不自动改 `is_valid`。

---

## 2. 入口与文件

- 入口脚本：`src/factor_monitor/factor_monitor_runner.py`
- 配置文件：仓库根 `config.ini`（开发环境自动用 `config_dev.ini`）
- 输出目录：`artifacts/factor_monitor/YYYY-MM/`
  - `factor_health_YYYY-MM-DD.json`
  - `factor_health_YYYY-MM-DD.md`

---

## 3. 当前默认口径（已锁定）

- `universe = ZZ500`
- `monitor_window_trading_days = 60`
- `warmup_trading_days = 200`
- `horizon_days = 5`（对数前向收益）
- 阈值来源：优先 `factor_threshold_config(scene=factor_monthly_monitor_ZZ500)`
- DB 无阈值时回退到配置中的 fallback 阈值
- 对账策略：`daily_csv` 抽检，缺文件 `skip+warn`

---

## 4. 配置说明（`[monitor]`）

常用项：

- `universe`：监控股票池
- `horizon_days`：前向收益持有期
- `monitor_window_trading_days`：监控窗口长度
- `warmup_trading_days`：预热长度（防 rolling/REF 起点失真）
- `threshold_scene`：阈值场景名
- `max_factors`：联调用，>0 时限制因子数量
- `parity_sample_factors` / `parity_sample_days`：对账抽样规模
- `parity_tolerance`：对账容差

---

## 5. 运行方式

示例（请在仓库根目录执行）：

```bash
python src/factor_monitor/factor_monitor_runner.py --config config.ini --as-of-date 2026-04-30
```

参数：

- `--config`：配置文件路径（非 prod 自动切 `_dev.ini`）
- `--as-of-date`：锚点日期（非交易日会向前对齐）

---

## 6. 输出内容

JSON 关键字段：

- `window`：本次监控窗口
- `thresholds`：阈值来源/版本/数值
- `factors[]`：每因子指标与 PASS/FAIL
- `prod_parity_check`：生产一致性对账结果

Markdown 报告用于人工快速浏览。

---

## 7. 依赖关系

本模块复用：

- `factor_docs.factor_docs_parser.load_all_factors`
- `factor_engine._load_stock_daily`
- `factor_engine.compute_factor_values`
- `factor_engine.winsorize_and_standardize`

因此需要保证：

- `factor_docs` 可正常解析因子公式
- `stock_daily` 数据完整

