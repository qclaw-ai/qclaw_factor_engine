## 模块：factor_engine（因子计算引擎）

> 职责：基于因子 DSL 公式和 `stock_daily` 行情数据，计算因子时间序列，并按约定做去极值 + 标准化，输出 `(trade_date, stock_code) -> factor_value`。

---

### 1. 配置说明：`factor_engine/config_*.ini`

#### 1.1 `[database]`

与其他模块保持一致，配置 PostgreSQL 连接：

```ini
[database]
db_host = 127.0.0.1
db_port = 5432
db_user = postgres
db_password = your_password_here
db_name = qclaw_factor_engine
```

#### 1.2 `[factor_engine]`

```ini
[factor_engine]
; 默认计算时间区间
start_date = 2024-01-01
end_date   = 2024-03-31

; 需要计算的因子ID列表（逗号分隔；留空表示“解析到的全部因子”）
factor_ids = FACTOR_DEMO_0001
```

- `start_date` / `end_date`：
  - 定义本次因子计算所覆盖的交易日期区间；
  - 必须确保对应区间内 `stock_daily` 已有行情数据，否则会返回空结果。
- `factor_ids`：
  - 为空：对 `factor_docs` 解析到的 **所有因子** 逐个计算；
  - 非空：只计算指定的部分因子。

> 生产环境建议：`config.ini` 与 `config_dev.ini` 分开维护，`start_date/end_date` 仅在“批量重算/初始化”时使用，日常调度可交给上层参数控制（见 3.2）。

---

### 2. 计算流程概览

入口脚本：`factor_engine_runner.py`，主要步骤：

1. 读取 `factor_engine/config_*.ini`。
2. 调用 `factor_docs.load_all_factors()` 获取 `FactorDefinition` 列表，并按 `factor_ids` 过滤。
3. 从 `stock_daily` 读取 `[start_date, end_date]` 区间内的行情数据：
   - 字段：`open, high, low, close, volume, turnover`；
   - 以 `(trade_date, stock_code)` 作为 MultiIndex。
4. 基于最小 DSL 执行公式：
   - 支持字段：`open, high, low, close, volume, turnover`
   - 支持函数：
     - `MA(x, n)`：按 `stock_code` 分组的 n 日简单移动平均
     - `REF(x, n)`：按 `stock_code` 分组向前平移 n 日
   - 运算符：`+ - * /`
5. 对原始因子值做预处理：
   - 每个交易日截面：
     - 1%–99% 分位去极值（winsorize）；
     - 对截面做 Z-score 标准化。
6. 输出：
   - 日志中打印每个因子的前 5 行样例；
   - 将完整结果导出为 CSV：
     - 路径：`factor_engine/output/<factor_id>_<start_date>_<end_date>.csv`
     - 列：`trade_date, stock_code, factor_value`。

---

### 3. 时间区间在生产环境的推荐配置

#### 3.1 一次性重算 / 初始化

场景：新因子首次上线或需要重新回溯某段历史时。

配置建议（只在需要时手动改）：

```ini
[factor_engine]
start_date = 2015-01-01
end_date   = 2026-03-11
factor_ids = FACTOR_xxx_1,FACTOR_xxx_2
```

- 手工运行：

```bash
ENV=prod python factor_engine/factor_engine_runner.py
```

- 这类“全量重算”通常不会作为定时任务，而是运维/研究手动触发，跑完即止。

#### 3.2 日常调度（推荐由调度器/上游脚本控制）

生产环境日常跑因子时，不一定要在 `config.ini` 里写死 `start_date/end_date`，可以：

- 在 `config.ini` 中只保留默认值（例如最近一年）：

```ini
[factor_engine]
start_date = 2024-01-01      ; 作为 fallback
end_date   = 2026-03-11
factor_ids =
```

- 上游调度脚本在调用时通过环境变量或 CLI 参数覆盖（后续可扩展支持，例如）：

```bash
ENV=prod FACTOR_START_DATE=2025-01-01 FACTOR_END_DATE=2025-12-31 \
python factor_engine/factor_engine_runner.py
```

当前 MVP 版本尚未实现 CLI 参数解析，**默认使用配置文件中的时间区间**。  
后续如需更灵活的窗口控制，可以在 `factor_engine_runner.py` 中增加参数解析逻辑，将环境变量或命令行参数优先级提高到配置之上。

> 实战建议：  
> - 初始化 / 复算：直接在 `config.ini` 里改时间区间 + 手工运行；  
> - 日常滚动计算：保持 `config.ini` 时间区间为一个“足够大的窗口”（例如最近两年），由调度层控制具体执行频率和是否写回 DB/缓存。

---

### 4. 后续对接回测与入库

- 目前 `factor_engine` 只负责生成标准化后的因子值并导出 CSV，尚未将结果写入数据库。  
- 后续 `backtest_core` 会基于相同的 `(trade_date, stock_code, factor_value)` 结构计算 IC / 分层收益等指标；  
- `backtest_io` 则负责将回测结果落地为 JSON 和 `factor_backtest` 记录；  
- `selection_and_store` 再根据阈值表 `factor_threshold_config` 决定是否将该因子写入 `factor_basic` / `factor_files`。

当前阶段只要保证：**给定某个因子 ID，就能稳定输出一份 CSV 因子序列**，后续模块就有了可靠输入。

