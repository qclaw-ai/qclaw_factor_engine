## 因子相关性矩阵模块 `factor_corr_matrix`

> 目标：为策略工厂提供标准化的因子相关性矩阵 `factor:corr:<yyyymmdd>`，用于因子去重；只由因子工厂写入，策略工厂只读。

---

## 1. 职责与整体流程

- 从数据库 `factor_basic` 中读取当前有效因子集合：
  - `SELECT factor_id FROM factor_basic WHERE is_valid = TRUE`
- 从本地因子结果目录（默认 `factor_values/`）读取各因子的截面因子值：
  - 文件由 `factor_engine_runner` 输出，命名约定：
    - `<factor_id>_<start_date>_<end_date>.csv`
    - 例如：`JQ_ALPHA_001_2024-01-01_2025-03-17.csv`
- 在给定时间窗口（默认最近 252 个自然日）上剪裁因子值时间序列：
  - index：`(trade_date, stock_code)` 多级索引
  - column：`factor_value`
- 拼接成宽表，按列计算 Pearson 相关系数矩阵：
  - 使用 `pandas.DataFrame.corr(method="pearson")`
- 按最小重叠交易日数过滤无意义相关性：
  - 对每对因子，统计各自在窗口内均有非 NaN 的交易日数；
  - 若重叠天数 `< min_overlap_days`（默认 120）则该对因子相关性视为“不可用”，不写入结果。
- 将结果写入 Redis，key/value 结构与 `docs/redis_conventions.md` 一致。

---

## 2. Redis key 与 value 结构

- **Key 模式**

  - `factor:corr:<yyyymmdd>`
    - 例如：`factor:corr:20260317`
  - 前缀可通过配置 `factor_corr.redis_key_prefix` 调整，默认 `factor:corr`

- **Value JSON 结构（MVP）**

  ```json
  {
    "as_of_date": "2026-03-17",
    "window": "252d",
    "corr": {
      "FACTOR_FD_20260310_001": {
        "FACTOR_JQ_20260310_001": 0.72,
        "FACTOR_BQ_20260310_001": 0.10
      },
      "FACTOR_JQ_20260310_001": {
        "FACTOR_FD_20260310_001": 0.72
      }
    }
  }
  ```

---

## 3. 配置文件说明 `config.ini`

> 实际运行时建议使用 `config_dev.ini`，并通过环境变量 `ENV` 控制；此处只说明字段。

### 3.1 `[database]` 段

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = user_xxx
db_password = password_xxx
db_name = db_factor
```

- 用于 `common.db.get_db_manager` 连接 PostgreSQL，查询 `factor_basic`。

### 3.2 `[factor_corr]` 段

```ini
[factor_corr]
; 是否启用该任务
enable = true

; 回看窗口长度（天数），用于构造最近窗口
window_days = 252

; 两个因子参与相关性计算/输出所需的最小重叠交易日数
min_overlap_days = 120

; Redis key 前缀，最终 key = <prefix>:<yyyymmdd>
redis_key_prefix = factor:corr

; Redis 中保留多少天历史 key（按 key 后缀 <yyyymmdd> 判断）
keep_days = 30

; 因子 CSV 输出目录，需与 backtest_core/factor_engine 配置一致
factor_output_dir = factor_values

; 可选：只对指定因子列表计算相关性（逗号分隔，不填则使用所有 is_valid=true 的因子）
; factor_ids = JQ_ALPHA_001,JQ_ALPHA_002

; 可选：显式指定计算日期（YYYY-MM-DD）；不填则默认使用当前 UTC 日期
; as_of_date = 2026-03-17
```

### 3.3 `[redis]` 段

```ini
[redis]
host = 127.0.0.1
port = 6379
db = 0
password =
```

---

## 4. 调用方式 / 调度集成

### 4.1 命令行入口

模块主入口：

- 文件：`src/factor_corr/factor_corr_matrix.py`
- 函数：`run_factor_corr_matrix(config_file: str = "src/factor_corr/config.ini")`

直接运行：

```bash
python -m factor_corr.factor_corr_matrix
```

或在项目根目录：

```bash
python src/factor_corr/factor_corr_matrix.py
```

> 注意：实际运行时请传入 `config_dev.ini` 路径，例如：
> `run_factor_corr_matrix(config_file="src/factor_corr/config_dev.ini")`。

### 4.2 与 APScheduler 集成（示意）

调度频率建议：**每日夜间一次**（例如 03:00），在日度回测/入库任务之后。

```python
from factor_corr.factor_corr_matrix import run_factor_corr_matrix

def job_factor_corr():
    run_factor_corr_matrix(config_file="src/factor_corr/config_dev.ini")

# APScheduler 配置略
```

---

## 5. 策略工厂侧使用约定（只读）

- 策略工厂从 Redis 中读取：

  1. 找到当日或最近日期的 `factor:corr:<yyyymmdd>`；
  2. 解析 JSON，获取 `payload["corr"]`；
  3. 按自身配置的阈值（例如 `abs(corr) > 0.7`）剔除高度相关因子。

- 策略工厂只读 `factor:*` key，不写入/修改这些 key。
- 如需调整 JSON 结构或 key 命名规则，应优先更新 `docs/redis_conventions.md`，并在必要时做兼容处理。

---

## 6. 与现有模块关系

- 依赖：
  - `factor_engine_runner` 产生的 `factor_values/*.csv`；
  - `selection_and_store_runner` 更新 `factor_basic.is_valid`；
  - `common.config` / `common.db` / `common.utils`（读取配置、连接 DB、日志）。
- 无反向写 DB 行为，仅读：
  - `factor_basic`：筛选有效因子；
  - 不修改 `factor_backtest` 等表。

> 简单理解：该模块是一个“纯派生视图”，把已有的因子表现组织成相关性矩阵，并通过 Redis 暴露给策略工厂。