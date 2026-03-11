## 模块：recheck_and_deprecate（因子复检与淘汰）

> 职责：对当前 `is_valid = true` 的因子，根据历史回测记录中初始 IC 与最新 IC 的衰减情况，结合阈值配置表 `factor_threshold_config`，判断因子是否“过时”，并更新 `factor_basic.is_valid` 等字段。

---

### 1. 配置说明：`recheck_and_deprecate/config_*.ini`

#### 1.1 `[database]`

与其他模块保持一致，用于访问 `factor_basic` / `factor_backtest` / `factor_threshold_config`：

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = postgres
db_password = your_password_here
db_name = qclaw_factor_engine
```

#### 1.2 `[recheck]`

```ini
[recheck]
scene = A_stock_daily_single_factor
start_date = 2021-01-01
end_date   = 2099-12-31
```

- `scene`：
  - 指定复检使用的阈值配置场景；
  - 当前实现直接复用 `A_stock_daily_single_factor` 中的：
    - `ic_decay_threshold`（IC 衰减比例阈值）
    - `latest_ic_min`（最新 IC 下限）。
- `start_date` / `end_date`：
  - 主要用于文档标记和今后扩展，当前实现只依赖 `factor_backtest` 中已有的回测记录；
  - 实际复检窗口（即回测使用的时间区间）由上游 `factor_engine + backtest_core + backtest_io` 在复检任务中控制。

---

### 2. 阈值与判定规则

从 `factor_threshold_config` 中读取：

```sql
SELECT *
FROM factor_threshold_config
WHERE scene = :scene AND is_active = TRUE
ORDER BY created_at DESC
LIMIT 1;
```

需要字段：

- `ic_decay_threshold`：IC 衰减比例阈值（例如 0.5 表示衰减超过 50% 触发条件之一）；
- `latest_ic_min`：最新 IC 的下限阈值。

对于每个因子，定义：

- `IC_init`：初始回测 IC 值（`factor_backtest` 中最早一条记录的 `ic_value`）；
- `IC_latest`：最新回测 IC 值（同一表中最新一条记录的 `ic_value`）；
- `IC_decay = (IC_init - IC_latest) / IC_init`（当 `IC_init > 0` 时）。

**过时判定规则（MVP 版本）：**

- 若 `IC_init <= 0` 或非常接近 0：
  - 不计算衰减比例，仅根据最新 IC：
    - 若 `latest_ic_min` 非空且 `IC_latest < latest_ic_min` → 判定为过时；
    - 否则视为仍有效。
- 若 `IC_init > 0`：
  - 需要同时满足两条：
    1. `IC_decay > ic_decay_threshold`
    2. `IC_latest < latest_ic_min`
  - 才判定为过时；
  - 否则视为仍有效。

---

### 3. 数据来源：`factor_basic` 与 `factor_backtest`

1. **候选因子集合**：  
   - 从 `factor_basic` 中筛选当前 `is_valid = true` 的因子；
   - 只对这些因子进行复检淘汰判断。

2. **初始与最新回测记录**：

使用一个 CTE 查询一次性取出每个因子的初始与最新 IC：

```sql
WITH fb AS (
    SELECT b.*
    FROM factor_backtest b
    JOIN factor_basic f ON f.factor_id = b.factor_id
    WHERE f.is_valid = TRUE
),
init AS (
    SELECT DISTINCT ON (factor_id)
        factor_id,
        ic_value AS ic_init,
        id       AS init_id,
        backtest_time AS init_time
    FROM fb
    ORDER BY factor_id, backtest_time ASC, id ASC
),
latest AS (
    SELECT DISTINCT ON (factor_id)
        factor_id,
        ic_value AS ic_latest,
        id       AS latest_id,
        backtest_time AS latest_time
    FROM fb
    ORDER BY factor_id, backtest_time DESC, id DESC
)
SELECT
    i.factor_id,
    i.ic_init,
    l.ic_latest,
    i.init_id,
    l.latest_id,
    i.init_time,
    l.latest_time
FROM init i
JOIN latest l USING (factor_id);
```

这保证每个因子至少有 1 条初始和 1 条最新记录。

---

### 4. 状态更新行为

对判定为“过时”的因子集合 `deprecate_ids`，执行：

```sql
UPDATE factor_basic
SET
  is_valid = FALSE,
  deprecate_reason = COALESCE(deprecate_reason, 'performance'),
  deprecate_time = :deprecate_time
WHERE factor_id = ANY(:factor_ids);
```

- 若 `deprecate_reason` 原本为空，则填入 `'performance'`；
- 若已有值（例如之前因别的原因淘汰），则保留原值。

> 当前实现 **不会删除或修改** 历史 `factor_backtest` 记录，只通过 `is_valid` 标记逻辑淘汰。

---

### 5. 与回测任务的配合方式

`recheck_and_deprecate` **不负责重新计算因子值与回测**，而是假设你已经通过下面的流水线产生了新的回测记录：

1. 基于当前 `is_valid = true` 的因子列表，调用：
   - `factor_engine`：使用新的复检时间窗口（例如 `2021-01-01 ~ 今天`）生成最新因子值 CSV；
   - `backtest_core`：生成新的 `BacktestResult`；
   - `backtest_io`：写入新的 JSON 与 `factor_backtest` 记录。
2. 然后运行：

```bash
python recheck_and_deprecate/recheck_and_deprecate_runner.py
```

- 脚本会对比 **历史初始 IC**（通常是初次入库时那条）与最新一条 IC，按照阈值规则更新 `is_valid`。

在实际生产调度中，可以设计一个“复检任务”工作流：

1. 只对当前 `is_valid=true` 的因子跑 `factor_engine + backtest_core + backtest_io`（复检窗口固定或随时间滚动）；  
2. 立即调用 `recheck_and_deprecate_runner` 更新状态。

---

### 6. 运行方式

在项目根目录执行：

```bash
python recheck_and_deprecate/recheck_and_deprecate_runner.py
```

日志会输出每个因子的：

- `ic_init` / `ic_latest`；
- 使用的阈值 `ic_decay_threshold` / `latest_ic_min`；
- 判定结果：`KEEP`（仍有效）或 `DEPRECATE`（淘汰）。

最后会给出本次淘汰因子数量及其 ID 列表。

---

### 7. 与其他模块的关系

- 与 `selection_and_store`：
  - `selection_and_store` 决定“新因子是否初次入库”（更新 `pass_standard` + `is_valid`）；  
  - `recheck_and_deprecate` 决定“已入库因子是否因表现衰退而被淘汰”（更新 `is_valid=false`）。
- 与 `reactivate_candidates`（后续扩展）：
  - 已被淘汰、`deprecate_reason='performance'` 的因子可以通过复活流程重新回测并恢复 `is_valid=true`。

当前 `recheck_and_deprecate` 提供了一条最小可用的“基于 IC 衰减 + 最新 IC 的淘汰机制”，后续可以在此基础上加入更多维度（如夏普、回撤、连续不合格次数等）来细化判定逻辑。

