## 因子工厂 MVP 阶段数据库使用约定

> 版本：v1（仅针对当前 MVP，实现简单可用为主）

---

### 1. 表作用边界

- `factor_basic`：因子基础元信息（名称、类型、股票池、周期、来源、有效状态等），**所有因子的中心表**。
- `factor_files`：因子对应的文件路径信息（md / 回测 JSON / 日志 / 可选缓存列 `factor_values_path`），**只负责“文件在哪”**。  
  - **`factor_values_path`**：批量因子值 CSV 的**兼容/缓存指针**；**权威路径**见 `factor_value_files`。`backtest_io` **不再**按本次回测域覆盖该列；需要与磁盘一致时可跑 `src/backtest_io/sync_factor_values_path_runner.py`（见 `docs/真分域收尾与策略工厂接入_落地步骤.md`）。  
- **`factor_value_files`**：按 `(factor_id, universe, artifact_type, …)` 存 **真分域因子值 CSV** 等路径（引擎写入 `batch_csv`）；详见 `docs/因子侧真分域因子值_设计与落地步骤.md`。策略工厂加载批量因子值时应**优先查此表**。
- 真分域路径约定（已定）：`factor_values/by_universe/{UNIVERSE}/{factor_id}_{UNIVERSE}_{start}_{end}.csv`；**ALL 也在 `by_universe/ALL/`，不再使用旧扁平 `factor_values/` 作为主路径**。
- `factor_backtest`：因子每一次回测的结果记录（IC/IC_IR/夏普/回撤/换手率等）；**多领域**时同一 `factor_id` 多行，以 `test_universe` 区分，并可有 `result_json_rel_path`。  
- `factor_universe_status`：因子在 **各实证域** 上是否过阈（`is_valid`）；**权威按域状态**。`factor_basic.is_valid` 为其派生：**任一侧为 TRUE 则为 TRUE**（供日更等兼容查询）。  
- `factor_threshold_config`：各场景的阈值配置（入库、复检、复活等）。
- `factor_candidate`：尚未进入正式因子库的候选因子队列（给非程序员/前端表单用）。
- `factor_definition`：**当前阶段不启用，仅作为后续“公式入库 + 版本管理”的预留设计**。

---

### 2. 因子文档与 `factor_files.doc_path` 约定

- `factor_files.doc_path` **统一存相对路径**，不要存绝对路径：
  - 示例：`factor_docs/FACTOR_FD_20260310_001.md`
- 各环境通过配置的根目录来区分，不改 DB 中的 `doc_path`：
  - dev：`factor_docs_dir = "D:/dev/qclaw_factor_engine/factor_docs"`
  - prod：`factor_docs_dir = "/mnt/qclaw/factor_docs"`
- 程序侧获取完整路径的方式（伪代码）：

```python
full_doc_path = os.path.join(config.factor_docs_dir, factor_files.doc_path)
```

- 回测 JSON / 日志路径同理，`backtest_json_path`、`log_path` 也尽量存相对路径（相对某个根目录，如 `backtest_results/`、`logs/backtest/`），具体根目录交由配置管理。

---

### 3. 公式来源与版本策略（MVP 阶段）

- 当前阶段：**所有“真公式”只存在于因子 Markdown 文档中**。
  - `factor_docs` 模块负责解析 md，拿到 DSL 表达式等信息。
  - `factor_engine` 只依赖 md 解析结果，不读 `factor_definition` 表。
- `factor_definition` 表在 `schema_mvp.sql` 中默认是注释掉的：
  - 如需启用，后续再：
    1. 取消建表语句注释；
    2. 写一次性迁移脚本，把现有 md 中的公式同步进 `factor_definition`；
    3. 调整 `factor_engine`，优先从 `factor_definition` 读取定义，读不到时再回退 md。

---

### 4. 实验/多版本因子使用约定

- **简单原则**：MVP 阶段不做“同一因子多版本”的精细追踪，**直接通过新 `factor_id` 区分版本**。
- 推荐命名示例：
  - 正式版：`FACTOR_FD_20260310_001`
  - 实验版 1：`FACTOR_FD_20260310_001_EXP1`
  - 实验版 2：`FACTOR_FD_20260310_001_EXP2`
- 对每个 `factor_id`：
  - `factor_basic`：各有一行记录。
  - `factor_files`：各有一行记录，`doc_path` 指向各自的 md 文件。
  - `factor_backtest`：各自在本因子下追加回测记录。
- 优点：
  - 实现简单、逻辑清晰；
  - 不需要在初期就引入 `factor_definition` 的版本复杂度；
  - 删除/停用实验版只需要把对应 `factor_basic.is_valid` 设为 `false` 即可。

---

### 5. 未来升级到 `factor_definition` 的方向（仅备注）

- 当你开始需要：
  - 为“同一逻辑因子”维护多个公式版本（v1/v2/v3）；
  - 在回测结果中精确追踪“用的是哪个版本的公式”；
  - 将部分研究/生产流程从 md 迁移到 DB 时，
- 再按设计文档《因子工厂自动化流程_MVP实现草图.md》第 7 节的方案：
  - 启用 `factor_definition`；
  - 在 `factor_backtest` 增加 `definition_version` 字段；
  - 让 `factor_engine`/`backtest_core` 携带版本信息。

> 当前约定的目标：**先让 MVP 端到端可跑、心智负担最低**，后续需要更强的版本管理时，以此为基础向前演进。

