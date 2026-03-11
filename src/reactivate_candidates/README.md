## 模块：reactivate_candidates（因子复活）

> 职责：针对已因表现不佳而被淘汰的因子（`is_valid=false 且 deprecate_reason='performance'`），在冷却期之后基于最新回测结果判断其是否“复活达标”，若达标则重新标记为有效。

---

### 1. 配置说明：`reactivate_candidates/config_*.ini`

#### 1.1 `[database]`

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = postgres
db_password = your_password_here
db_name = qclaw_factor_engine
```

#### 1.2 `[reactivate]`

```ini
[reactivate]
scene = A_stock_daily_single_factor
cooldown_days = 180
```

- `scene`：
  - 指定在 `factor_threshold_config` 中复用哪一类阈值；
  - 当前实现复用 `A_stock_daily_single_factor` 中的复活字段：
    - `ic_min_reactivate`
    - `ic_ir_min_reactivate`
    - `sharpe_min_reactivate`
    - `max_drawdown_max_reactivate`
- `cooldown_days`：
  - 冷却期天数，只有淘汰时间 `deprecate_time <= NOW() - cooldown_days` 的因子才具备复活资格。

---

### 2. 候选因子筛选规则

从 `factor_basic` 中筛选“具备复活资格”的因子：

```sql
SELECT factor_id
FROM factor_basic
WHERE is_valid = FALSE
  AND deprecate_reason = 'performance'
  AND deprecate_time IS NOT NULL
  AND deprecate_time <= (NOW() - (:cooldown_days || ' days')::interval);
```

说明：

- 只对因“表现差”（`performance`）被淘汰的因子考虑复活；
- 需要等待一个冷却期（默认 180 天）以避免频繁在“好/坏”之间来回切换。

---

### 3. 最新回测结果与复活阈值

#### 3.1 最新回测记录

对候选因子 ID 集合，从 `factor_backtest` 中取各自的最新回测记录：

```sql
SELECT DISTINCT ON (factor_id)
    id,
    factor_id,
    backtest_period,
    horizon,
    ic_value,
    ic_ir,
    sharpe_ratio,
    max_drawdown,
    turnover,
    backtest_time
FROM factor_backtest
WHERE factor_id = ANY(:factor_ids)
ORDER BY factor_id, backtest_time DESC, id DESC;
```

> 注意：复活流程本身**不跑回测**，要求你在此之前已经通过 `factor_engine + backtest_core + backtest_io` 为这些候选因子生成了最新一轮回测记录。

#### 3.2 复活判定规则

从 `factor_threshold_config` 中获取：

- `ic_min_reactivate`
- `ic_ir_min_reactivate`
- `sharpe_min_reactivate`
- `max_drawdown_max_reactivate`

对每个候选因子的最新回测记录 `rec`，判定是否复活：

```text
若配置 ic_min_reactivate            则要求 ic_value      >= ic_min_reactivate
若配置 ic_ir_min_reactivate         则要求 ic_ir         >= ic_ir_min_reactivate
若配置 sharpe_min_reactivate        则要求 sharpe_ratio  >= sharpe_min_reactivate
若配置 max_drawdown_max_reactivate  则要求 max_drawdown >= max_drawdown_max_reactivate
```

- 若所有已配置的条件均满足 → 判定为 `REACTIVATE`；
- 否则判定为 `KEEP_INACTIVE`（保持淘汰状态）。

> 同样，`max_drawdown_max_reactivate` 通常为负数，表示“复活允许的最大回撤”，回测结果必须 `>=` 该阈值。

---

### 4. 数据库更新行为

对判定为 `REACTIVATE` 的因子集合 `to_reactivate`：

```sql
UPDATE factor_basic
SET
  is_valid = TRUE,
  reactivated_time = :reactivated_time
WHERE factor_id = ANY(:factor_ids);
```

- `reactivated_time`：记录最近一次被重新启用的时间；
- `deprecate_reason` 通常保留原值（例如 `performance`），方便日后追踪其生命周期。

> 当前实现**不会自动修改** `factor_backtest.pass_standard` 或 `factor_files` 等字段，  
> 这些字段在复活回测 + `selection_and_store` 执行后会自然保持一致。

---

### 5. 推荐的复活任务流程

完整的“复活任务”可以拆成两步：

1. **生成最新回测记录**（针对候选淘汰因子）：
   - 从 `factor_basic` 中筛选 `is_valid=false AND deprecate_reason='performance' AND deprecate_time <= NOW()-cooldown_days`；
   - 对这批因子跑：
     1. `factor_engine_runner`（按复活窗口生成因子值）；
     2. `backtest_core_runner`（计算最新回测指标）；
     3. `backtest_io_runner`（写入 JSON + `factor_backtest`）。
2. **执行复活判定**：

```bash
python reactivate_candidates/reactivate_candidates_runner.py
```

- 脚本将：
  - 再次根据冷却期过滤候选因子；
  - 读取其最新回测记录与阈值；
  - 更新符合条件的因子的 `is_valid=true, reactivated_time=NOW()`。

---

### 6. 运行方式

在项目根目录执行：

```bash
python reactivate_candidates/reactivate_candidates_runner.py
```

日志中会输出每个候选因子的：

- 最新 IC / IC_IR / Sharpe / MaxDD；
- 使用的复活阈值；
- 最终决策：`REACTIVATE` 或 `KEEP_INACTIVE`；
- 最终统计本次复活通过的因子数量及其 ID。

---

### 7. 与其他模块的关系

- 与 `recheck_and_deprecate`：
  - `recheck_and_deprecate` 用于将表现显著衰退的因子从 `is_valid=true` → `false`（逻辑淘汰）；
  - `reactivate_candidates` 用于将长冷却期后表现重新达标的因子从 `is_valid=false` → `true`。
- 与 `selection_and_store`：
  - `selection_and_store` 决定“新因子是否初次入库”；
  - `recheck_and_deprecate` / `reactivate_candidates` 则负责入库因子的生命周期管理（淘汰/复活）。

当前 `reactivate_candidates` 提供了一条最小可用的“复活通路”，后续可以根据需要引入更多指标或额外约束（例如限定连续多次复检均达标才允许复活等）。 

