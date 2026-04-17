## 因子工厂 & 策略工厂 Redis 使用约定（MVP）

> 版本：v1  
> 目标：统一 Redis key 命名与职责边界，避免不同模块相互踩坑。

---

### 1. 实例与 DB 使用策略

- 推荐方案：**单实例 + 单 DB（一般为 DB 0）**，通过 **key 前缀** 区分不同业务域。

- 理由：
  - Redis 多 DB（0 以外）在云托管 / 集群场景支持有限，后面不好迁移；
  - 单 DB + 前缀更利于监控和清理，也便于未来做分片；
  - 简化运维心智：所有 key 都在一个 keyspace 里。

- 连接配置示例（放在 ini / yaml 里）：

  ```ini
  [redis]
  host = 127.0.0.1
  port = 6379
  db = 0
  password = ...
  ```

---

### 2. 总体前缀命名空间

- 因子相关前缀：`factor:*`
- 策略相关前缀：`strategy:*`
- 公共/元数据前缀（如有）：`meta:*`

约定：

- 因子工厂（`qclaw_factor_engine`）负责写入 / 维护 `factor:*` 命名空间下的 key；
- 策略工厂（`qclaw_strategy_engine`）可以**只读**其中部分 key（例如相关性矩阵、因子值缓存）；
- 策略工厂自己的缓存使用 `strategy:*` 命名空间，因子侧一般不读不写。

---

### 3. 因子侧 Redis key 约定（由因子工厂负责）

#### 3.1 因子相关性矩阵

- Key 模式：

  - `factor:corr:<yyyymmdd>`

- 含义：

  - 存放基于最近一段历史（如近 1 年）计算的**因子相关性矩阵**，`<yyyymmdd>` 为计算日期。
  - 例如：`factor:corr:20260316`。

- Value 建议格式（MVP）：

  - 建议使用 JSON 字符串（后续可根据需要改为压缩二进制）：

    ```json
    {
      "as_of_date": "2026-03-16",
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

- 生命周期：

  - 因子工厂定期（例如每日或每周）重算一次相关性矩阵，写入新的 `factor:corr:<yyyymmdd>`；
  - 保留近若干个版本（例如 30 天），超出窗口的 key 通过脚本清理或设置 TTL 过期。

- 策略侧使用方式（只读）：

  - `strategy_factor_list_loader`：
    - 优先使用当日或最近日期的 `factor:corr:<yyyymmdd>`；
    - 按配置的相关性阈值（如 `> 0.7`）剔除高度相关因子。

#### 3.2 因子值缓存（可选，后续扩展）

> MVP 阶段可以先不实现；当需要提升多因子回测性能时再启用。

- 推荐 key 模式：

  - `factor:values:<factor_id>:<yyyymmdd>`
  - 例如：`factor:values:FACTOR_FD_20260310_001:20260316`

- 含义：

  - 存放某个 `factor_id` 在某个交易日的**全市场截面因子值**；
  - 由因子工厂在因子计算 / 回测后写入，策略工厂在多因子打分时只读。

- Value 建议：

  - 可根据性能需求选择：
    - 压缩二进制（如 pandas DataFrame → feather/arrow → gzip）；
    - 或简化版 JSON（用于小规模 / 调试环境）。

- 生命周期：

  - 建议只缓存近一段时间（例如最近 1 年）的因子值；
  - 老数据主要存在 PostgreSQL / 文件系统中，Redis 只做热数据缓存。

---

### 4. 策略侧 Redis key 约定（由策略工厂负责，因子侧一般不关心）

> 以下前缀由 `qclaw_strategy_engine` 使用，这里只列出以避免命名冲突，具体含义由策略仓库文档定义。

- 可能的 key 形态（示例）：

  - `strategy:backtest_cache:<strategy_id>:<backtest_hash>`  
    - 缓存某策略在给定参数组合下的回测结果，避免重复计算。

  - `strategy:config:<strategy_id>`  
    - 缓存策略配置 JSON，减少频繁读文件/DB。

  - `strategy:template:<template_name>`  
    - 缓存厚模板的元信息（版本、最近更新时间等）。

因子侧约束：

- 不在 `strategy:*` 命名空间下写入任何 key；
- 如果需要调试，可只读这些 key，不改变值。

---

### 5. 权责边界总结

- 因子工厂（`qclaw_factor_engine`）：

  - 负责生产并写入：
    - `factor:corr:*`（因子相关性矩阵，策略侧依赖）
    - （可选）`factor:values:*`（因子截面值缓存）

- 策略工厂（`qclaw_strategy_engine`）：

  - **只读**上述 `factor:*` key；
  - 自己写入 `strategy:*` 命名空间下的缓存。

- 双方约定：

  - 不在对方命名空间下写 key；
  - 如需调整 key 结构（前缀或 value schema），需先在文档中更新说明，并保证必要的向后兼容或提供迁移脚本。

---

### 6. 监控与清理建议

- 监控：

  - 分别对 `factor:*` 和 `strategy:*` keyspace 做统计（数量、内存占用）；
  - 发现 key 数异常增长时，优先检查是否有未设置 TTL 的临时缓存。

- 清理：

  - 对带日期后缀的 key（如 `factor:corr:<yyyymmdd>`、`factor:values:*:<yyyymmdd>`）：
    - 建议在写入新版本时顺便清理超出保留窗口的旧 key（例如只保留最近 30 天）。
  - 对临时缓存 key，尽量设置合理 TTL，避免长期占用内存。

