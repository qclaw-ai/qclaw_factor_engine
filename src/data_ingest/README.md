## 模块：data_ingest（行情数据导入）

> 职责：从 AkShare 拉取 A 股日线数据，写入 PostgreSQL 的 `stock_daily` 表。支持全量导入与日更两种模式，以及多种股票池配置。

---

### 1. 依赖与运行环境

- Python 3.11
- 依赖包（已写在项目根目录的 `requirements.txt` 中）：
  - `SQLAlchemy`
  - `psycopg2-binary`
  - `akshare`

安装依赖（在项目根执行）：

```bash
pip install -r requirements.txt
```

---

### 2. 配置说明：`data_ingest/config_*.ini`

#### 2.1 `[database]`

PostgreSQL 连接信息（字段名与其他模块保持一致）：

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = postgres
db_password = your_password_here
db_name = qclaw_factor_engine
```

#### 2.2 `[paths]`

预留给后续使用（与整体项目保持一致）：

```ini
[paths]
factor_docs_dir = ./factor_docs
backtest_results_dir = ./backtest_results
logs_dir = ./logs
```

#### 2.3 `[data_ingest]`：核心参数

```ini
[data_ingest]
; 导入模式：
;   full  = 全量导入，按 start_date / end_date 拉取；
;   daily = 日更模式，自动按回看天数计算 start_date。
mode = full

; 股票池类型：
;   ALL_A  = 全 A 股（通过 AkShare 的 stock_zh_a_spot 获取）
;   HS300  = 沪深300 成分股（指数代码 000300）
;   ZZ500  = 中证500 成分股（指数代码 000905）
;   CUSTOM = 仅使用 stock_codes 中配置的股票列表
universe = CUSTOM

; 仅当 universe = CUSTOM 时生效，使用逗号分隔，支持 .SZ/.SH 后缀
stock_codes = 000001.SZ,600000.SH

; 时间区间（mode = full 时生效）
start_date = 2024-01-01
end_date   = 2024-03-31

; 日更模式的回看天数（mode = daily 时生效，单位：自然日）
; 例如 5 表示 [今天-5天, 今天] 区间内的数据会被重新拉取并 upsert。
daily_lookback_days = 5

; AkShare 的前复权方式：qfq / hfq / None
adjust = qfq
```

---

### 3. 股票池解析逻辑（`universe`）

代码中通过 `_resolve_universe(cfg)` 计算最终股票代码列表：

- `CUSTOM`：
  - 直接解析 `stock_codes` 字符串为列表（保留 .SZ/.SH 后缀不变）。
- `ALL_A`：
  - 调用 `ak.stock_zh_a_spot()` 获取所有 A 股；
  - 根据返回的 `代码` 与 `市场/市场编号` 字段补齐 `.SH` 或 `.SZ` 后缀；
  - 忽略无法识别交易所的记录。
- `HS300`：
  - 调用 `ak.index_stock_cons(symbol="000300")` 获取沪深300成分；
  - 使用 `品种代码/代码` 作为基础代码，按首位是否为 `6` 判断上交所/深交所并补后缀。
- `ZZ500`：
  - 同理，使用 `ak.index_stock_cons(symbol="000905")` 获取中证500成分。

这样，通过修改 `universe` 即可快速切换股票池，无需在配置中维护大规模代码列表。

---

### 4. 导入模式（`mode`）

#### 4.1 全量导入：`mode = full`

- 使用配置中的 `start_date` 与 `end_date` 作为时间窗口；
- 对股票池中的每只股票：
  - 调用 `ak.stock_zh_a_hist` 拉取该区间的日线数据；
  - 将字段映射为：
    - `stock_code, trade_date, open, high, low, close, volume, turnover`
  - 使用 `INSERT ... ON CONFLICT (trade_date, stock_code) DO UPDATE` 写入 `stock_daily` 表，实现幂等更新。

适用于首次初始化或需要重新刷一段历史的场景。

#### 4.2 日更导入：`mode = daily`

- 忽略配置中的 `start_date`；
- 自动计算：
  - `end_date = 今天`
  - `start_date = 今天 - daily_lookback_days`
- 其他逻辑与全量导入相同（仍使用 upsert），通过“回看 N 天”保证即使某天任务失败也能在后续补齐数据。

---

### 5. 运行方式

在项目根目录执行（Windows/Linux 都一样）：

```bash
python data_ingest/data_ingest_stock_daily.py
```

说明：

- `common.config.Config` 会根据环境变量 `ENV` 自动选择：
  - 非 prod：`data_ingest/config_dev.ini`
  - prod：`data_ingest/config.ini`
- 日志会输出：
  - 使用的时间区间、universe、股票数量；
  - 每只股票的拉取记录数与写入结果；
  - 最终总写入/更新记录数。

---

### 6. 与整体因子工厂的关系

- 本模块负责填充基础行情表 `stock_daily`；
- 后续 `factor_engine` 将直接从 `stock_daily` 中按 `trade_date, stock_code` 维度读取数据，计算因子值；
- 因此，确保本模块的导入**幂等且稳定**对于整个因子流水线至关重要。

