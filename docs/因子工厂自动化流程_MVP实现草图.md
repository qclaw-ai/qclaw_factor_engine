## 因子工厂自动化流程 MVP 实现草图

> 目标：先把「因子文档 → 因子计算 → 单因子回测 → 入库 → 复检/淘汰」全流程跑通，公式不复杂、自研一个最小版 IC/分层回测，不依赖复杂栈；所有阈值从配置（数据库或 ini）读取，而不是写死在代码里。

---

## 一、整体架构概览

- **技术栈**：Python + PostgreSQL（云端因子库） + AkShare +（可选）Alphalens  
- **核心流程链路（MVP）**：
  1. `data_ingest`：从 AkShare 拉取 A 股日线数据，写入云端 PostgreSQL 的 `stock_daily` 表  
  2. `factor_crawler`：定时/手动从指定网站抓取因子，生成固定模板的 Markdown 文档（候选因子）  
  3. `factor_docs`：解析因子 Markdown 文档，产出统一的因子定义对象  
  4. `factor_engine`：基于最小 DSL 公式和 `stock_daily` 计算因子值并做预处理（去极值 + 标准化）  
  5. `backtest_core`：自研轻量回测，计算分层收益、IC/IC_IR、夏普、最大回撤、换手率等指标  
  6. `backtest_io`：将回测结果写成 JSON 文件，并在云端因子库的 `factor_backtest` 表中记录一条回测记录  
  7. `selection_and_store`：读取阈值配置（`factor_threshold_config`），筛选合格因子，并写入云端因子库的 `factor_basic` 与 `factor_files`  
  8. `recheck_and_deprecate`：定期对库内 `is_valid=true` 的因子重新回测，对比初始与最新指标，根据阈值判定是否过时并更新 `factor_basic.is_valid`

- **配置与阈值管理**：
  - 所有因子筛选与过时判定阈值均存放在配置层（如 PostgreSQL 中的 `factor_threshold_config` 表或 ini 文件），代码中不写死数值；
  - 回测窗口、horizon（MVP 固定为 5d）、分组数等统一在配置或常量模块中管理。

- **数据与文件**：
  - 行情与因子元数据、回测结果结构化记录都在云端 PostgreSQL 中（因子库本身）；
  - 因子 Markdown 文档与回测 JSON/日志文件存放在文件系统指定目录，通过 `factor_files` 表与因子 ID 关联。

## 二、统一基础约定

### 2.1 因子公式 DSL 最小子集

- **支持运算符**：
  - `+`, `-`, `*`, `/`
- **支持函数**：
  - `MA(x, n)`：n 日简单移动平均
  - `REF(x, n)`：向前平移 n 日
- **支持字段**：
  - `open, high, low, close, volume, turnover`（来自 `stock_daily`）
- **示例**：
  - `20日动量`：`(close - REF(close, 20)) / REF(close, 20)`
  - `5日相对均线强度`：`close_5 / MA(close, 20)`（`close_5` 为预先构造或从文档说明中解析）

### 2.2 因子预处理默认规则（MVP）

- 缺失值：有 NaN 的样本在当日截面内直接剔除  
- 去极值：按 **当日截面 1%–99% 分位数** 剪裁  
- 标准化：按 **当日截面全市场 Z-score**（不做行业/规模中性）  
- 中性化：**MVP 不做**（后续版本再扩展）

### 2.3 未来收益标签定义（MVP）

- 默认 horizon：`5d`  
- 未来收益：  
  - `ret_5d = ln(close_t+5 / close_t)`  
- 是否重叠持仓：允许重叠（标准横截面因子评估）

---

## 三、数据表与配置概览

### 3.1 PostgreSQL 数据表（MVP 必需）

1. **`stock_daily`（A 股日行情）**

- **字段（最小集）**：
  - `stock_code`：varchar，股票代码
  - `trade_date`：date，交易日期
  - `open`：numeric
  - `high`：numeric
  - `low`：numeric
  - `close`：numeric
  - `volume`：numeric
  - `turnover`：numeric
- **索引**：
  - `(trade_date, stock_code)` 复合索引

2. **`factor_basic`（因子基础信息）**

- **字段（MVP）**：
  - `factor_id`：varchar，主键
  - `factor_name`：varchar
  - `factor_type`：varchar（如 动量/量价 等）
  - `test_universe`：varchar（如 全A/沪深300）
  - `trading_cycle`：varchar（如 日线）
  - `source_url`：varchar
  - `create_time`：timestamp
  - `is_valid`：boolean（当前是否有效）
  - `deprecate_reason`：varchar，淘汰原因（如 `performance` / `bug` / `data_issue` 等）
  - `deprecate_time`：timestamp，最近一次被标记为无效的时间
  - `reactivated_time`：timestamp，最近一次被重新启用的时间（可选）
- **说明**：仅在因子通过入库筛选后才插入/更新。

3. **`factor_backtest`（因子回测记录）**

- **字段（MVP）**：
  - `id`：serial，主键
  - `factor_id`：varchar，外键到 `factor_basic.factor_id`
  - `backtest_period`：varchar（如 "2021-01-01 至 2025-12-31"）
  - `horizon`：varchar（如 "5d"）
  - `ic_value`：numeric
  - `ic_ir`：numeric
  - `sharpe_ratio`：numeric
  - `max_drawdown`：numeric
  - `turnover`：numeric
  - `pass_standard`：boolean
  - `backtest_time`：timestamp

4. **`factor_files`（因子文档与文件路径）**

- **字段（MVP）**：
  - `factor_id`：varchar，主键
  - `doc_path`：varchar（因子 md 文件路径）
  - `backtest_json_path`：varchar（最新一次回测 JSON 路径）
  - `log_path`：varchar（回测日志路径）

5. **`factor_threshold_config`（阈值配置表）**

- **字段（MVP）**：
  - `id`：serial，主键
  - `scene`：varchar（如 "A_stock_daily_single_factor"）
  - `version`：varchar（如 "v1"）
  - `ic_min`：numeric
  - `ic_ir_min`：numeric
  - `sharpe_min`：numeric
  - `max_drawdown_max`：numeric
  - `turnover_max`：numeric
  - `is_active`：boolean
  - `created_at`：timestamp
  - `comment`：text

### 3.2 配置文件（ini / yaml）

- **示例段落**：
  - `[database]`：host, port, user, password, dbname  
  - `[paths]`：`factor_docs_dir`, `backtest_results_dir`, `logs_dir`  
  - `[backtest_defaults]`：`start_date`, `end_date`（或 `years_window`）、`horizon=5d`  

---

## 四、模块设计详情

### 模块 A：数据更新 `data_ingest`

#### A.1 主要职责

- 从 AkShare 拉取 A 股日线数据（前复权或不复权，按统一口径）  
- 将数据写入 PostgreSQL `stock_daily` 表  
- 处理简单的数据质量问题（空值、异常值）并打日志

#### A.2 主要配置参数

- `database` 连接信息（来自 ini）  
- `market`：如 `"cn_a_stock"`  
- `start_date`, `end_date`：初始全量导入区间  
- 日更模式下：根据当前日期自动推算 `start_date = 上一个交易日`  

#### A.3 输入输出

- **输入**：
  - 外部：AkShare HTTP/SDK  
  - 内部：无（首次全量），或最近已更新日期  
- **输出**：
  - 写入/更新 `stock_daily` 表记录  
  - 日志文件：`logs/data_ingest_YYYYMMDD.log`

---

### 模块 B：因子抓取与生成文档 `factor_crawler`

#### B.1 主要职责

- 从 3 个指定网站（factors.directory / BigQuant / 聚宽）抓取因子相关内容  
- 各站点使用独立适配器模块，统一输出中间结构 `RawFactor` 列表  
- 将 `RawFactor` 转换为固定模板的 Markdown 文档，写入 `factor_docs_dir`

#### B.2 主要配置参数

- `sources.fd.base_url`：factors.directory 基础地址  
- `sources.bq.base_url`：BigQuant 论坛基础地址  
- `sources.jq.base_url`：聚宽社区基础地址  
- `sources.search_keywords`：按站点配置的检索关键词列表  
- `paths.factor_docs_dir`：因子文档输出根目录  
- `crawler.timeout`、`crawler.max_retries`、`crawler.user_agent` 等爬虫相关参数  
- `crawler.max_factors_per_run`：单次任务最多生成的因子数量（防止一次性过多）

#### B.3 输入输出

- **输入**：
  - 外部：各网站 HTML/JSON 页面内容  
  - 内部：已有因子列表（来自 `factor_basic` 或扫描 `factor_docs_dir`，用于名称/公式去重）  
- **输出**：
  - 若干 `RawFactor` 结构（在内部适配器层使用）：
    - `name, raw_formula, description, type, universe, period, direction, url`  
  - 标准化 Markdown 文档文件（通过统一文档生成器，例如 `factor_doc_builder` 生成）：
    - 命名：`FACTOR_<来源缩写>_<日期>_<序号>.md`  
    - 内容字段固定对齐《操作手册_v2》步骤1 模板：  
      - 因子ID、因子名称、公式（已转为最小 DSL）、描述、类型、适用股票池、调仓周期、因子方向、来源URL  
  - 抓取日志：`logs/factor_crawler_YYYYMMDD.log`

#### B.4 内部适配器划分

- `factor_crawler_fd`：factors.directory 适配器，利用其结构化页面直接提取公式/描述等字段  
- `factor_crawler_bq`：BigQuant 适配器，解析帖子正文与代码块，优先从简单的 `factor = ...` 行中提取公式  
- `factor_crawler_jq`：聚宽适配器，针对以“因子介绍”为主题的帖子提取相关字段，复杂策略代码在 MVP 阶段可跳过  
- 以上适配器接口统一：`fetch_factors() -> List[RawFactor]`，由 `factor_crawler` 统一调度和去重处理

### 模块 C：因子文档解析 `factor_docs`

#### C.1 主要职责

- 扫描 `factor_docs_dir` 下的 Markdown 文件  
- 基于统一模板解析出因子元数据和表达式  
- 产出结构化对象供后续模块调用

#### C.2 主要配置参数

- `paths.factor_docs_dir`：因子文档根目录  
- 文档模板（逻辑上固定在代码中）：  
  - 需要有：`因子ID、因子名称、公式、描述、类型、适用股票池、调仓周期、因子方向、来源URL`

#### C.3 输入输出

- **输入**：
  - 文件系统中的 `.md` 文件  
- **输出**：
  - 内存中的结构化对象（例如 Python dict）：
    - `factor_id`
    - `factor_name`
    - `formula`（DSL 字符串）
    - `description`
    - `factor_type`
    - `test_universe`
    - `trading_cycle`
    - `factor_direction`（`long`/`short`）
    - `source_url`
    - `doc_path`

---

### 模块 D：因子计算引擎 `factor_engine`

#### D.1 主要职责

- 基于 DSL 公式 + 行情数据计算因子值时间序列  
- 应用预处理规则（去极值、标准化）  
- 输出 `(trade_date, stock_code) -> factor_value` 的结构化结果

#### D.2 主要配置参数

- `backtest_defaults.start_date`, `backtest_defaults.end_date` 或回测窗口策略  
- 预处理规则（MVP 可在代码中写死）：
  - 去极值：1%–99% 截断  
  - 标准化：Z-score（全样本截面）

#### D.3 输入输出

- **输入**：
  - 因子定义对象（来自 `factor_docs`）  
  - 行情数据（从 `stock_daily` 查询而来）  
- **输出**：
  - DataFrame / 表格型结构：  
    - index：`(trade_date, stock_code)`  
    - column：`factor_value`  
  - 可选：中间过程中记录的预处理参数（如截断阈值）仅用于 debug

---

### 模块 E：回测核心 `backtest_core`

#### F.1 主要职责

- 基于因子值和未来收益标签，计算单因子的关键评价指标：  
  - IC / IC_IR  
  - 分层收益（10 组）及多空收益曲线  
  - 夏普比率、最大回撤、turnover（简化定义）

#### F.2 主要配置参数

- `backtest_defaults.horizon`（MVP 固定 `5d`）  
- 分组数量：`n_quantiles = 10`（写在代码里即可）  
- 多空构建方式：Top 组多头 - Bottom 组空头

#### F.3 输入输出

- **输入**：
  - 因子值表：`(trade_date, stock_code) -> factor_value`  
  - 收盘价表：`(trade_date, stock_code) -> close`（从 `stock_daily` 获取）  
  - 因子方向：`factor_direction`（若为 `short`，可在此处统一取反）  
- **输出**（`BacktestResult` 结构体）：
  - `factor_id`
  - `backtest_period`
  - `horizon`（如 `"5d"`）
  - `ic_value`
  - `ic_ir`
  - `sharpe_ratio`
  - `max_drawdown`
  - `turnover`
  - 其他如分层收益曲线可选择不写入 DB，只放在 JSON 里

---

### 模块 E：回测结果落地 `backtest_io`

#### F.1 主要职责

- 接收 `BacktestResult`，生成标准化回测结果 JSON 文件  
- 写入一条记录到 `factor_backtest` 表  
- 打回测日志

#### F.2 主要配置参数

- `paths.backtest_results_dir`：回测 JSON 存放目录  
- `paths.logs_dir`：回测日志目录

#### F.3 输入输出

- **输入**：
  - `BacktestResult` 对象  
  - 因子元信息（名称、测试股票池等）  
- **输出**：
  - JSON 文件 `backtest_results/<factor_id>_backtest.json`  
    - 字段示例（与手册一致，略）：  
      - `factor_id, factor_name, backtest_period, test_universe, trading_cycle, horizon, key_metrics{...}, pass_standard, backtest_time, log_path`
  - `factor_backtest` 表记录（对应一条历史回测）  
  - 回测日志文件 `logs/backtest/<factor_id>.log`

---

### 模块 F：因子筛选与入库 `selection_and_store`

#### G.1 主要职责

- 读取最新一次回测结果与阈值配置  
- 判断是否通过入库标准  
- 写入/更新 `factor_basic` 与 `factor_files` 表  
- 对不合格因子进行归档（不入库）

#### G.2 主要配置参数

- 当前生效的 `factor_threshold_config`：
  - `scene="A_stock_daily_single_factor" AND is_active=true`  
- `paths.factor_docs_dir`, `paths.backtest_results_dir`, `paths.logs_dir`

#### G.3 输入输出

- **输入**：
  - 单个或批量因子的最新回测结果（来自 `factor_backtest` 或 JSON）  
  - 阈值配置（来自 `factor_threshold_config`）  
- **输出**：
  - 若通过：  
    - `factor_basic`：插入/更新一条记录，`is_valid=true`  
    - `factor_files`：插入/更新对应路径  
  - 若不通过：  
    - 不改动 `factor_basic`（MVP 可选择不建记录）  
    - 将文档与回测 JSON 归档到某个 `not_passed/` 目录，并写明原因到日志  

---

### 模块 G：复检与过时判定 `recheck_and_deprecate`

#### H.1 主要职责

- 对当前因子库中 `is_valid=true` 的因子进行重新回测（用最新数据）  
- 对比初始回测与最新回测指标，按照阈值配置判定因子是否过时  
- 更新 `factor_basic.is_valid` 状态（逻辑淘汰）

#### H.2 主要配置参数

- `backtest_defaults`：复检的时间区间策略（MVP 可固定为 2021-01-01 至今）  
- 过时判定阈值（来自 `factor_threshold_config`，建议同一表不同字段或另一条记录）：
  - `ic_decay_threshold`（如 0.5）  
  - `latest_ic_min`（如 0.08）  
  - （以及连续复检不合格次数等扩展逻辑）

#### H.3 输入输出

- **输入**：
  - 来自 `factor_basic` 的有效因子列表（`is_valid=true`）  
  - 对应的初始回测记录（`factor_backtest` 中最早一条或指定标记为“初始回测”的记录）  
- **输出**：
  - 新增的回测记录写入 `factor_backtest`（最新一次）  
  - 若判定为过时：  
    - 更新 `factor_basic.is_valid=false`  
  - 回溯/状态更新日志文件：`logs/recheck_*.log`

---

## 五、MVP 流程视角下的端到端步骤

### 5.1 新因子发现与筛选链路（从网站到因子库）

1. 触发新因子抓取任务  
   - 由调度器（APScheduler）或人工运行脚本触发：`factor_crawler.run()`。  
   - 本步骤仅负责“这次要不要去网站扫一轮新因子”，不涉及回测。

2. 从网站抓取因子并生成标准文档（模块 B：`factor_crawler`）  
   - 针对每个站点适配器（`factor_crawler_fd` / `factor_crawler_bq` / `factor_crawler_jq`）：  
     - 根据配置的 `search_keywords` 抓取页面内容，解析出 `RawFactor` 列表：  
       - 字段包含：`name, raw_formula, description, type, universe, period, direction, url`。  
   - 将不同站点的 `RawFactor` 汇总后：  
     - 与已有因子（`factor_basic` + 现有 `factor_docs`）比对做名称/公式去重；  
     - 将 `raw_formula` 转换为约定的最小 DSL（只支持四则运算 + `MA/REF` 等基础函数），解析失败的因子本次跳过并记录日志。  
   - 经去重和 DSL 转换后的 `RawFactor`，统一通过固定模板生成 Markdown 文档：  
     - 命名：`FACTOR_<来源缩写>_<日期>_<序号>.md`；  
     - 字段固定：因子ID、因子名称、公式（DSL）、描述、类型、适用股票池、调仓周期、因子方向、来源URL。  
   - 文档写入 `factor_docs_dir`，并在 `logs/factor_crawler_YYYYMMDD.log` 记录抓取情况。

3. 解析因子文档（模块 C：`factor_docs`）  
   - 扫描本次新增的 Markdown 文档（可以通过文件时间戳或命名规则筛选）。  
   - 按固定模板解析出因子定义对象，字段至少包括：  
     - `factor_id, factor_name, formula(DSL), factor_type, test_universe, trading_cycle, factor_direction, source_url, doc_path`。  
   - 将解析后的因子定义放入待回测队列，供后续模块使用。

4. 计算因子值并执行回测（模块 D + E + F：`factor_engine` + `backtest_core` + `backtest_io`）  
   - `factor_engine`：  
     - 从 `stock_daily` 表拉取指定股票池与时间区间（MVP 默认 2021-01-01 至今）的行情数据；  
     - 按因子 DSL 公式计算出 `(trade_date, stock_code) -> factor_value`；  
     - 对每个交易日截面执行统一预处理：  
       - 1%–99% 分位去极值；  
       - 全市场 Z-score 标准化；  
     - 输出因子值表供回测使用。  
   - `backtest_core`：  
     - 使用 `horizon = 5d` 构造未来收益标签 `ret_5d = ln(close_t+5 / close_t)`；  
     - 按每日横截面对因子值排序，切分为 10 等分分组，统计各组未来 5 日平均收益；  
     - 计算 IC（秩相关）、IC_IR、分层多空收益序列、夏普比率、最大回撤以及换手率（简化定义）；  
     - 若因子方向为 `short`，在计算前将因子值统一乘以 `-1`，保证评价指标方向一致。  
   - `backtest_io`：  
     - 将回测结果写成 JSON 文件：`backtest_results/<factor_id>_backtest.json`；  
     - 在 `factor_backtest` 表中插入一条回测记录（包含 `ic_value, ic_ir, sharpe_ratio, max_drawdown, turnover, horizon` 等）；  
     - 记录回测日志到 `logs/backtest/<factor_id>.log`。

5. 根据阈值配置筛选合格因子并写入因子库（模块 G：`selection_and_store`）  
   - 从 `factor_threshold_config` 中读取当前激活版本（`scene="A_stock_daily_single_factor" AND is_active=true`）的阈值：  
     - `ic_min, ic_ir_min, sharpe_min, max_drawdown_max, turnover_max` 等。  
   - 对每个新因子的最新回测记录进行判定：  
     - 若所有核心指标均满足阈值 → `pass_standard = true`；  
     - 否则 `pass_standard = false`，并记录具体未达标原因。  
   - 对于通过筛选的因子（视为“合格因子”）：  
     - 在 `factor_basic` 中插入或更新一条记录：  
       - 写入 `factor_id, factor_name, factor_type, test_universe, trading_cycle, source_url, create_time, is_valid=true`；  
     - 在 `factor_files` 中插入或更新对应记录：  
       - `factor_id, doc_path, backtest_json_path, log_path`。  
   - 对于未通过筛选的因子：  
     - 不在 `factor_basic` 中建记录（或建记录但 `is_valid=false`，视实现而定）；  
     - 将 md 与回测 JSON 移动到 `not_passed/` 目录中归档，并在日志中写明失败原因。  
   - 至此，合格因子已写入统一的云端因子库（当前即为 PostgreSQL 中的 `factor_basic + factor_backtest + factor_files` 三张表）。

---

### 5.2 库内因子定期复检与淘汰链路（对库内因子回溯，判断是否过时）

1. 触发复检任务  
   - 由 APScheduler 定时（例如每月 1 日凌晨）或人工脚本触发：`recheck_and_deprecate.run()`。  
   - 本任务的目标是：对因子库中当前 `is_valid=true` 的因子重新回测，并根据配置判断是否过时。

2. 批量对库内有效因子重新回测（复用模块 C + D + E：`factor_docs` + `factor_engine` + `backtest_core` + `backtest_io`）  
   - 从 `factor_basic` 中查询 `is_valid=true` 的因子列表，得到一组 `factor_id`。  
   - 对每个因子：  
     - 使用 `factor_docs` 读取并解析对应 Markdown 文档，得到因子定义（公式、股票池、周期、方向等）；  
     - 使用 `factor_engine` 在包含最新交易日的数据区间上重新计算因子值（预处理规则与初次回测保持一致）；  
     - 使用 `backtest_core` 执行回测，生成最新一轮 IC/IC_IR/夏普/回撤等指标；  
     - 使用 `backtest_io` 将本次复检结果追加到 `factor_backtest` 表，并生成新的回测 JSON 与日志文件。  

3. 根据过时判定阈值判断每个因子是否“过时”（模块 H：`recheck_and_deprecate` 核心逻辑）  
   - 从 `factor_threshold_config` 中读取当前生效的“过时判定”阈值配置（可与入库阈值为同表不同字段或不同记录）：  
     - 例如：`ic_decay_threshold`（IC 衰减比例阈值）、`latest_ic_min`（最新 IC 下限）、`max_bad_checks`（允许连续不合格次数）等。  
   - 对每个因子：  
     - 从 `factor_backtest` 中取出“初始回测记录”（第一次入库时的那条）与“最近一次回测记录”；  
     - 计算 IC 衰减率：`(IC_init - IC_latest) / IC_init`；  
     - 结合最新 IC 值、IC_IR、夏普等指标，与阈值配置进行对比：  
       - 若满足“过时”条件（例如：IC 衰减率大于 `ic_decay_threshold` 且 最新 IC 小于 `latest_ic_min` 等） → 判定为“过时”；  
       - 否则判定为“仍有效”。

4. 更新因子库中的状态并记录复检日志  
   - 对判定为“过时”的因子：  
     - 将 `factor_basic.is_valid` 更新为 `false`（逻辑淘汰，不物理删除历史记录与文件）；  
     - 在复检日志中记录淘汰原因（包括初始/最新 IC、衰减率、阈值版本等信息）。  
   - 对判定为“仍有效”的因子：  
     - 保持 `is_valid=true` 不变；  
     - 无需更改基础信息，仅通过新增的 `factor_backtest` 记录反映其最新表现。  
   - 整体复检过程的运行情况（起止时间、因子数量、淘汰数量等）写入 `logs/recheck_YYYYMMDD.log`。

5. （可选）基于复检结果进行后续分析或报警  
   - 根据本次复检中被淘汰的因子数量/占比，触发监控报警或人工复核；  
   - 为后续调整阈值配置（`factor_threshold_config`）提供依据。


## 六、后续扩展的预留点（非 MVP 必需）

- `factor_engine`：增加更多 DSL 函数（如 `STD`, `MAX`, `MIN` 等）  
- `backtest_core`：支持多 horizon（5d/10d/20d）指标对比  
- `factor_threshold_config`：为不同 `scene` 建立不同阈值集  
- 新表 `factor_lifecycle`：用于记录状态变化（created/backtested/deprecated 等）  
- 与 APScheduler 集成：  
  - 周期性触发 `data_ingest`、`recheck_and_deprecate` 等脚本

## 七、后续升级：在数据库中存储因子定义（设计预留）

> 本节为后续升级方案说明，MVP 阶段可以不实现。当前版本依然以 Markdown 文档中的 DSL 公式为唯一“真源”，数据库只存文档路径。

### 7.1 新增表：`factor_definition`

用于在数据库中显式存储每个因子的 DSL 公式与关键定义，支持版本管理。

- **推荐字段**：

  - `id`：serial，主键  
  - `factor_id`：varchar，外键指向 `factor_basic.factor_id`  
  - `dsl_expr`：text，因子的 DSL 公式（与 Markdown 中保持一致）  
  - `data_domain`：varchar（如 `price`/`fundamental`）  
  - `preprocess_config`：jsonb（如 `{ "winsorize": "q1_99", "standardize": "zscore_all" }`）  
  - `default_horizon`：varchar（如 `"5d"`）  
  - `version`：varchar（如 `"v1"`）  
  - `is_active`：boolean（当前是否使用该版本定义）  
  - `created_at`：timestamp  
  - `comment`：text（备注）

- **关系约束**：

  - 一个 `factor_id` 可对应多条定义记录（不同 `version`），但在任一时刻只允许一条 `is_active=true`。  
  - `factor_basic` 仍然只存元信息（名称、类型、周期、股票池等），不直接存公式。

### 7.2 与现有模块的关系（升级后的走向）

> 括号内说明哪些模块在升级时需要改，MVP 阶段可以不动，只是预留设计。

- `factor_crawler`（可选改）  
  - 现在：抓取后只生成 Markdown 文档。  
  - 升级：在生成 md 的同时，直接插入/更新一条 `factor_definition`（写入 `dsl_expr` 等），保证 DB 与 md 同步。

- `factor_docs`（可选弱化）  
  - 现在：**必须**解析 md 才能拿到公式。  
  - 升级后：  
    - 可以优先从 `factor_definition` 拿 `dsl_expr` 和配置；  
    - md 只用于文档展示/人工检查。

- `factor_engine`（需要轻改）  
  - 现在：从 `factor_docs` 的解析结果中取 `formula(DSL)`。  
  - 升级后：  
    - 优先从 `factor_definition` 读取当前 `is_active=true` 且 `version` 最新的一条记录，拿 `dsl_expr` 和 `preprocess_config`；  
    - 若 DB 无记录，再回退到解析 md。

- `backtest_core / backtest_io / selection_and_store`  
  - 逻辑不变，只是可以在回测结果和阈值判断时，把 `definition_version` 一并写入，方便以后追踪：  
    - 在 `factor_backtest` 增加字段 `definition_version`（对应 `factor_definition.version`）。

### 7.3 渐进式迁移步骤（以后真要升的时候照做）

1. **初始化填充**：  
   - 写一个一次性迁移脚本：扫描现有 `factor_docs_dir` 中的 md；  
   - 解析出 `factor_id + 公式(DSL)` 等字段；  
   - 为每个因子在 `factor_definition` 中插入一条 `version="v1", is_active=true` 的记录。

2. **调整 `factor_engine` 读数据源顺序**：  
   - 优先从 `factor_definition` 读定义；  
   - 读不到时才从 md 解析。

3. （可选）**慢慢把研究流程改成“直接改 DB 的 DSL + 版本号”，而不是编辑 md**：  
   - md 作为“公式说明文档”，`factor_definition` 作为“生产定义”。  
   - 回测结果的 JSON/DB 里都携带 `definition_version`，方便以后对比“v1 vs v2”的效果差异。

> 当前阶段你只需要记住：**MVP 不建 `factor_definition` 表，所有计算仍然以 Markdown 为准**；这节只是把以后“怎么把公式搬进 DB”讲清楚，方便你后面演进时有明确落点。

## 八、因子复活流程设计（从已淘汰因子中重新启用）

> 目标：对于因子库中历史上因表现不佳而被淘汰（`is_valid=false` 且 `deprecate_reason=performance`）的因子，定期或手动重新回测；若最近一段时间表现重新达标，则将该因子重新标记为有效并纳入后续流程。

### 8.1 字段与配置补充（在现有表基础上的小改动）

- `factor_basic`（新增/补充字段建议）：
  - `deprecate_reason`：varchar，淘汰原因（如 `performance` / `bug` / `data_issue` 等）
  - `deprecate_time`：timestamp，最近一次被标记为无效的时间
  - `reactivated_time`：timestamp，最近一次被重新启用的时间（可选）

- `factor_threshold_config`（新增一条“复活场景”配置）：
  - `scene = 'A_stock_daily_factor_reactivate'`
  - 字段示例：
    - `ic_min_reactivate`
    - `ic_ir_min_reactivate`
    - `sharpe_min_reactivate`
    - `max_drawdown_max_reactivate`
  - 一般可设置为 **不低于** 初始入库阈值，甚至略高，用来过滤掉短期噪声。

### 8.2 复活流程模块：`reactivate_candidates`

#### 8.2.1 触发方式

- 支持两种触发：
  - 定时：由 APScheduler 每季度或每半年触发一次复活任务；
  - 手动：研究/运维人员按需运行脚本 `reactivate_candidates.run()`。

#### 8.2.2 候选因子筛选

从 `factor_basic` 中筛选出“具备复活资格”的因子列表：

- 条件示例：
  - `is_valid = false`
  - `deprecate_reason = 'performance'`（只对因“表现差”被淘汰的因子尝试复活）
  - `deprecate_time <= now() - cooldown_period`（如冷却期 6 个月）

得到候选 `factor_id` 集合，作为本次复活任务的处理对象。

#### 8.2.3 对候选因子进行重新回测（复用现有回测模块）

对每一个候选因子，复用现有模块执行一轮“最新表现”回测：

1. **加载因子定义**（模块 C：`factor_docs`）  
   - 根据 `factor_id` 在 `factor_files` / `factor_docs_dir` 中找到对应 Markdown 文档；  
   - 解析出 DSL 公式、适用股票池、周期、方向等。

2. **计算因子值**（模块 D：`factor_engine`）  
   - 从 `stock_daily` 中读取包含最新交易日的行情数据，时间窗口建议为 **最近 1–2 年**；  
   - 按 DSL 公式计算 `(trade_date, stock_code) -> factor_value`；  
   - 应用与初次回测一致的预处理：1%–99% 去极值 + 全市场 Z-score。

3. **执行回测**（模块 E：`backtest_core`）  
   - 使用 `horizon=5d` 构造未来收益标签并计算 IC/IC_IR、分层收益、夏普、最大回撤、turnover 等；  
   - 若 `factor_direction = short`，在计算前对因子值整体乘以 `-1`。

4. **记录此次复活回测结果**（模块 F：`backtest_io`）  
   - 将回测结果写入 JSON 文件（可命名为 `<factor_id>_backtest_reactivate.json`）；  
   - 向 `factor_backtest` 表插入一条新记录，建议增加或利用一个标记字段区分类型：
     - 如新增字段 `backtest_type`，取值 `initial` / `recheck` / `reactivate`；
     - 或在 `comment` 字段中注明 `"type": "reactivate"`。

#### 8.2.4 根据“复活阈值”判定是否重新启用

1. **读取“复活场景”阈值配置**  
   - 从 `factor_threshold_config` 中读取 `scene='A_stock_daily_factor_reactivate' AND is_active=true` 的那条记录；
   - 取出 `ic_min_reactivate, ic_ir_min_reactivate, sharpe_min_reactivate, max_drawdown_max_reactivate` 等字段。

2. **对每个候选因子的最新回测记录做判断**  
   - 示例规则（可根据实际调整）：
     - `ic_value >= ic_min_reactivate`
     - `ic_ir >= ic_ir_min_reactivate`
     - `sharpe_ratio >= sharpe_min_reactivate`
     - `max_drawdown <= max_drawdown_max_reactivate`
   - 若全部满足 → 视为“复活通过”；否则视为“复活失败”。

#### 8.2.5 更新因子状态与生命周期记录

- **复活通过的因子**：
  - 在 `factor_basic` 中：
    - 将 `is_valid` 更新为 `true`；
    - 更新 `reactivated_time = now()`；
    - `deprecate_reason` 可保留（用于历史追溯），也可根据需要清空或标注为 `performance_recovered`。
  - 在日志或生命周期表（若后续实现）中记录一条事件：
    - `event_type = 'reactivated'`，附带字段：
      - 复活回测指标（IC/IC_IR/夏普等）；
      - 使用的阈值配置版本（`threshold_config.version`）；
      - 上一次淘汰时间与原因。

- **复活失败的因子**：
  - 保持 `is_valid=false` 不变；
  - 在本次复活任务的日志中记录失败原因（如 IC/夏普仍未达标等），方便后续分析。

#### 8.2.6 输入输出总结

- **输入**：
  - `factor_basic` 中已淘汰的因子列表（含淘汰原因与时间）；  
  - `factor_files` 中的因子文档路径；  
  - `stock_daily` 行情数据；  
  - `factor_threshold_config` 中“复活场景”的阈值配置。

- **输出**：
  - 新增的复活回测记录：插入到 `factor_backtest`；  
  - 更新后的因子状态：部分因子在 `factor_basic` 中从 `is_valid=false` 变为 `is_valid=true`；  
  - 复活任务日志：`logs/reactivate_YYYYMMDD.log`，记录任务统计（候选数、通过数、失败数）及关键原因说明。

## 九、因子候选队列与非程序员接入机制设计

> 目标：让非程序员（研究员、投资经理等）也能把从新网站、新论文中发现的因子思路纳入本系统，但不要求他们编写 DSL 或代码。系统通过“候选因子队列 + 标准化转写”接入自动流水线。

### 9.1 因子候选表 `factor_candidate` 设计

用于记录所有“尚未进入正式因子库”的候选因子，无论来源是网站、论文还是内部报告。

- **推荐字段**：

  - `candidate_id`：serial，主键  
  - `status`：varchar，候选状态枚举：
    - `submitted`：已提交，待研究员/程序员处理
    - `parsed`：已由研究员/程序员解析出标准信息，但尚未生成正式因子
    - `ready_for_backtest`：已生成标准 Markdown 文档，对应 `factor_id`，可进入自动回测流程
    - `rejected`：不采纳（无效/重复/实现成本过高等）
  - `source_type`：varchar，来源类型（`website` / `paper` / `report` / `other`）
  - `source_ref`：text，来源引用（URL、DOI、文件路径等）
  - `raw_title`：text，原始标题（网页标题、paper 标题等）
  - `raw_description`：text，自然语言描述，由提交人填写或从网页中抓取
  - `raw_formula_snippet`：text，可选的公式/代码片段（原文 copy 即可）
  - `suggested_universe`：varchar，提交人认为适用的股票池（如 全A/沪深300）
  - `suggested_period`：varchar，提交人认为的调仓周期（如 日/周/月）
  - `suggested_type`：varchar，提交人主观判断的因子类型（动量/价值/情绪等）
  - `linked_factor_id`：varchar，正式因子 ID（如 `FACTOR_FD_20260310_001`，生成标准 md 后回填）
  - `created_by`：varchar，提交人
  - `created_at`：timestamp，提交时间
  - `updated_at`：timestamp，最近状态更新时间
  - `reject_reason`：text，若为 `rejected`，记录具体原因

> 说明：MVP 阶段可以先只实现 `submitted` → `ready_for_backtest` → `rejected` 三个状态，`parsed` 可在后续扩展。

### 9.2 非程序员提交入口（前端/表单层面的约定）

为非程序员提供一个简单的“因子候选提交表单”（Web 页面、内部工具或其他方式），字段与 `factor_candidate` 表中的部分字段对应：

- **表单必填字段**：

  - 因子名称（自由命名）
  - 来源类型 `source_type`（website/paper/report/other）
  - 来源链接/引用 `source_ref`（URL、DOI 或文件路径）
  - 因子思路描述 `raw_description`（自然语言即可）
  - 适用股票池 `suggested_universe`（从预设选项中选择，例如 全A/沪深300/中证500）
  - 调仓周期 `suggested_period`（日/周/月）

- **表单选填字段**：

  - `raw_formula_snippet`：看到的公式或代码片段，直接复制原文
  - `suggested_type`：因子大致类型（动量/价值/质量/情绪等）

- **提交流程**：

  - 用户提交表单 → 在 `factor_candidate` 表中插入一条记录，`status = 'submitted'`；  
  - 系统返回一个 `candidate_id` 作为后续追踪编号。

### 9.3 研究员/程序员侧的“标准化接入流程”

研究员/程序员定期处理 `factor_candidate` 中 `status='submitted'` 的记录，将有价值的候选因子转化为系统可回测的标准因子。

#### 9.3.1 候选筛选与初步评估

- 查询 `status='submitted'` 的候选，按提交时间或来源进行筛选；
- 对每条候选进行初步判断：
  - 明显无效/重复的 → 直接标记为 `rejected`，写入 `reject_reason`；
  - 有潜在价值的 → 进入下一步标准化。

#### 9.3.2 标准化为系统因子（生成 DSL + Markdown）

对选中的候选因子，执行以下步骤：

1. **确定正式因子 ID**  
   - 按既定命名规则生成 `factor_id`（如 `FACTOR_<来源缩写>_<日期>_<序号>`）。

2. **编写 DSL 公式**  
   - 从 `raw_formula_snippet`、paper 或网页描述中抽象出可计算的公式；
   - 转写为系统支持的最小 DSL（四则运算 + `MA/REF` 等）；
   - 人工或半自动（可以用 OpenClaw 做初稿）完成。

3. **补充标准化元数据**  
   - 确定：
     - `factor_type`（动量/价值/量价等）
     - `test_universe`（正式股票池）
     - `trading_cycle`（日线/周线）
     - `factor_direction`（long/short）
   - 将这些信息与 DSL 公式一起写入标准 Markdown 模板，形成 `FACTOR_xxx.md`。

4. **写入文档与队列更新**  
   - 将生成的 Markdown 文档保存到 `factor_docs_dir`；
   - 更新 `factor_candidate`：
     - `status = 'ready_for_backtest'`
     - `linked_factor_id = <生成的 factor_id>`

从这一刻起，这个候选因子就可以被视为一个“普通新因子”，进入自动回测流水线。

### 9.4 与现有自动流水线的衔接

- 后续流程完全复用现有模块：

  1. `factor_docs`：从 `factor_docs_dir` 读取新生成的 md，解析为因子定义对象；
  2. `factor_engine`：计算因子值；
  3. `backtest_core`：执行回测，生成 IC/ICIR/夏普等指标；
  4. `backtest_io`：写 JSON 与 `factor_backtest` 记录；
  5. `selection_and_store`：根据阈值配置判断是否入库，若通过则在 `factor_basic`/`factor_files` 中落地。

- 对于已生成 `linked_factor_id` 且 `status='ready_for_backtest'` 的 `factor_candidate`，  
  - 可以在 `selection_and_store` 成功入库后，将其 `status` 更新为：
    - `ready_for_backtest` → 保留（表示已接入流水线）；或  
    - 额外增加一个状态 `archived`，表示“已处理且因子已入库/拒绝”。

### 9.5 总结：非程序员的职责边界

- 非程序员 **只负责**：
  - 发现新因子来源（网站/paper/报告）；
  - 在统一表单中填写：链接、描述、基本属性（股票池/周期等）；
  - 通过 `candidate_id` 查询处理状态。

- 研究员/程序员 **负责**：
  - 评估候选有效性，决定是否采纳；
  - 将候选转写为 DSL + 标准 Markdown 文档；
  - 让已标准化的因子进入自动回测与入库流程。

> 这样设计后，系统既对“新来源/新思路”开放入口，又确保所有进入正式因子库的因子都经过可复现、可审计的标准化与回测流程。

> 本文仅定义模块边界、配置项、数据表与 I/O 约定，不包含具体代码实现，便于后续按本草图拆分仓库结构与开发任务。