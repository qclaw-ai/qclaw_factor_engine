## 模块：selection_and_store（因子筛选与入库）

> 职责：根据阈值配置表 `factor_threshold_config` 与最新回测结果 `factor_backtest`，判断因子是否通过入库标准，更新 `factor_backtest.pass_standard`、`factor_basic.is_valid`，并维护 `factor_files` 中的文档/回测 JSON 路径。

---

### 1. 配置说明：`selection_and_store/config_*.ini`

#### 1.1 `[database]`

用于读写 `factor_basic` / `factor_backtest` / `factor_files` / `factor_threshold_config`：

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = postgres
db_password = your_password_here
db_name = qclaw_factor_engine
```

#### 1.2 `[selection]`

```ini
[selection]
scene = A_stock_daily_single_factor
```

- `scene`：指定在 `factor_threshold_config` 中使用哪一类阈值配置。
  - 例如：`A_stock_daily_single_factor`。
  - 脚本会从该表中选择 `scene = :scene AND is_active = true` 的最新一条记录作为当前阈值。

#### 1.3 `[paths]`

```ini
[paths]
backtest_results_dir = backtest_results
```

- 与 `backtest_io` 的 `backtest_results_dir` 保持一致，用于生成 `factor_files.backtest_json_path`。

---

### 2. 阈值配置表使用方式：`factor_threshold_config`

当前脚本主要使用字段：

- `ic_min`：IC 最小值（入库）；
- `ic_ir_min`：IC_IR 最小值；
- `sharpe_min`：夏普最小值；
- `max_drawdown_max`：最大回撤上限（通常为负数，例如 `-0.2` 表示不超过 -20%）；
- `turnover_max`：换手率上限。

其他字段（如复检/复活相关的 `ic_decay_threshold` 等）保留给 `recheck_and_deprecate` 等模块使用，本模块暂不涉及。

**典型插入示例**（仅供参考，可按需调整数值）：

```sql
INSERT INTO factor_threshold_config (
    scene, version,
    ic_min, ic_ir_min, sharpe_min,
    max_drawdown_max, turnover_max,
    ic_decay_threshold, latest_ic_min,
    ic_min_reactivate, ic_ir_min_reactivate,
    sharpe_min_reactivate, max_drawdown_max_reactivate,
    is_active, created_at, comment
) VALUES (
    'A_stock_daily_single_factor', 'v1',
    0.02, 0.5, 0.8,
    -0.2, 0.6,
    0.5, 0.02,
    0.03, 0.6,
    1.0, -0.15,
    TRUE, NOW(), 'MVP 默认阈值配置'
);
```

---

### 3. 筛选逻辑

1. 从 `factor_threshold_config` 读取当前激活阈值：
   - `scene = selection.scene AND is_active = true`；
   - 若找不到记录，脚本会抛错并退出。

2. 从 `factor_backtest` 中读取每个因子最新一条记录：
   - 使用 PostgreSQL 的 `DISTINCT ON (factor_id)`：
     - 按 `factor_id, backtest_time DESC, id DESC` 排序；
     - 只保留每个 `factor_id` 的最新一行。

3. 对每个因子，判断是否通过：

```text
若配置了 ic_min        则要求 ic_value      >= ic_min
若配置了 ic_ir_min     则要求 ic_ir         >= ic_ir_min
若配置了 sharpe_min    则要求 sharpe_ratio  >= sharpe_min
若配置了 max_drawdown_max 则要求 max_drawdown >= max_drawdown_max
若配置了 turnover_max  则要求 turnover     <= turnover_max

任何一条不满足则视为不通过。
若某个阈值字段为 NULL，则该约束跳过。
```

> 注意：`max_drawdown_max` 通常为负数，表示“最大允许回撤”。  
> 例如阈值为 `-0.2`，则 `max_drawdown` 需要 `>= -0.2` 才算合格（-10% > -20%，更好）。

---

### 4. 数据库更新行为

对每个有最新回测记录的因子：

1. **更新 `factor_backtest.pass_standard`**

```sql
UPDATE factor_backtest
SET pass_standard = :pass_standard
WHERE id = :id;
```

2. **更新 `factor_basic.is_valid`**

```sql
UPDATE factor_basic
SET is_valid = :is_valid
WHERE factor_id = :factor_id;
```

- 通过 → `is_valid = true`  
- 不通过 → `is_valid = false`

> 当前实现假设 `factor_basic` 已由 `backtest_io` 或其他模块插入占位记录。  
> 若某因子暂未出现在 `factor_basic` 中，`UPDATE` 不会产生记录，这种情况在 MVP 阶段可以忽略或通过 `backtest_io` 保证。

3. **维护 `factor_files`**

- 利用 `factor_docs` 中的 `doc_path` 与 `backtest_results_dir`：

```sql
INSERT INTO factor_files (
  factor_id, doc_path, backtest_json_path, log_path
)
VALUES (...)
ON CONFLICT (factor_id) DO UPDATE SET
  doc_path = COALESCE(EXCLUDED.doc_path, factor_files.doc_path),
  backtest_json_path = COALESCE(EXCLUDED.backtest_json_path, factor_files.backtest_json_path);
```

- `doc_path`：
  - 来自 `FactorDefinition.doc_path`；
- `backtest_json_path`：
  - 形如 `backtest_results/<factor_id>_backtest.json`；
  - 若找不到对应 JSON 文件，则保持 `NULL` 并在日志中给出 warning。
- `log_path`：
  - 当前未使用，写入 `NULL`，留待后续扩展。

---

### 5. 运行方式

在项目根目录执行：

```bash
python selection_and_store/selection_and_store_runner.py
```

脚本会：

1. 读取 `selection_and_store/config_*.ini`；
2. 从 `factor_threshold_config` 中加载当前阈值；
3. 从 `factor_backtest` 抽取每个因子最新回测记录；
4. 对每个因子打日志：PASS / FAIL + 核心指标；
5. 更新 `factor_backtest.pass_standard`、`factor_basic.is_valid`、`factor_files` 对应记录。

日志输出便于你在生产环境中审计本次筛选的整体情况。

---

### 6. 与后续模块的关系

- `recheck_and_deprecate`：
  - 会基于 `factor_backtest` 的历史记录以及 `factor_threshold_config` 中的复检阈值，决定是否将因子淘汰（更新 `factor_basic.is_valid=false`）。
- `reactivate_candidates`：
  - 会基于复活场景的阈值对已淘汰因子进行重新回测并可能重新启用。

当前 `selection_and_store` 只处理“**最新一次回测是否通过入库标准**”这一环节，为因子库建立初始的“合格/不合格”标签。

