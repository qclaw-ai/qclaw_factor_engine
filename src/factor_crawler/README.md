## 模块：factor_crawler（因子抓取与生成文档）

> 职责：从外部网站（如 factors.directory / BigQuant / 聚宽）抓取因子相关内容，统一生成标准化的 Markdown 文档，写入项目根目录下的 `factor_docs/md/`，供 `factor_docs_parser` 后续解析。

当前版本仅实现了整体骨架与 `factors.directory` 的占位适配器，方便你后面按站点逐步补充抓取逻辑。

---

### 1. 配置说明：`src/factor_crawler/config_*.ini`

#### 1.1 `[crawler]`

```ini
[crawler]
; 启用的数据源列表，逗号分隔：
;   fd = factors.directory
;   bq = BigQuant
;   jq = 聚宽
sources = fd

; 单次任务最多生成的因子数量（防止一次抓太多）
max_factors_per_run = 10
```

- `sources`：
  - 控制本次运行要启用哪些站点适配器；
  - 当前仅实现 `fd`（factors.directory）的占位逻辑。
- `max_factors_per_run`：
  - 用来限制单次生成的因子数量，避免一次写入过多 md；
  - 每个数据源会在内部按这个上限截断。

#### 1.2 `[sources.*]`

以 `factors.directory` 为例：

```ini
[sources.fd]
base_url = https://factors.directory
search_keywords = momentum,value
```

- `base_url`：站点基础地址；
- `search_keywords`：抓取时使用的搜索关键词列表（逗号分隔），  
  后续在适配器中可以按关键词过滤页面或接口。

BigQuant / 聚宽段目前只是预留，后续需要时可以在配置和代码中一并实现。

#### 1.3 `[paths]`

```ini
[paths]
; 因子 Markdown 文档输出根目录（相对项目根）
factor_docs_dir = factor_docs/md
```

- 与整个项目其他模块保持一致，所有因子 md 都放在根目录 `factor_docs/md/` 下；
- `factor_docs_parser` 和后续流水线都依赖这一约定。

---

### 2. 核心数据结构：`RawFactor`

在 `fd_crawler.py` 中定义：

```python
@dataclass
class RawFactor:
    source: str
    name: str
    raw_formula: str
    description: str
    type: str
    universe: str
    period: str
    direction: str
    url: str
```

- `source`：来源标识（如 `fd` / `bq` / `jq`）；
- `name`：因子名称；
- `raw_formula`：从网页上抓到的原始公式（后续会转为 DSL 放入 md 中的 `公式(DSL)` 行）；
- `description`：文字描述；
- `type`：因子类型（动量/价值等）；
- `universe`：适用股票池（如 HS300 / ALL_A）；
- `period`：调仓周期（如 日线）；
- `direction`：`long` / `short`；
- `url`：来源页面链接。

目前的 `fd_crawler.fetch_factors(...)` 只是占位实现，后续真实抓取逻辑也应返回这个结构。

---

### 3. 入口脚本：`factor_crawler_runner.py`

#### 3.1 运行方式

在项目根目录执行：

```bash
python src/factor_crawler/factor_crawler_runner.py
```

脚本会：

1. 读取 `config_*.ini` 中的 `sources`、`max_factors_per_run` 与 `factor_docs_dir`；
2. 针对每个启用的数据源调用对应适配器（当前只有 `fd` 的占位版）：
   - `fetch_fd_factors(base_url, search_keywords)` → `List[RawFactor]`；
3. 对返回的 `RawFactor` 列表：
   - 按日期和来源生成因子ID：

```python
FACTOR_<SRC>_<YYYYMMDD>_<序号>
例如：FACTOR_FD_20260311_001
```

   - 将字段写入标准 md 模板：

```md
# 因子说明文档

因子ID: FACTOR_FD_20260311_001  
因子名称: xxx  
公式(DSL): ...  
描述: ...  
因子类型: ...  
适用股票池: ...  
调仓周期: ...  
因子方向: long  
来源URL: ...
```

   - 文件路径：`factor_docs/md/<factor_id>.md`。

#### 3.2 日志与输出

- 日志会记录：
  - 每个数据源抓取的因子数量；
  - 实际生成的 md 文件名与路径；
  - 若某数据源未实现适配器或未抓到任何因子，会有对应 warning。

---

### 4. 当前状态 & 后续扩展

当前 `factor_crawler` 主要完成了：

- 标准化配置结构；
- `RawFactor` 数据结构；
- 各数据源统一的 `fetch_factors(...) -> List[RawFactor]` 接口约定；
- 将 `RawFactor` 转为标准 md 并写入 `factor_docs/md/` 的流程；
- 运行入口与日志管理。

尚未实现的部分（需要你以后按站点逐步补齐）：

- `fd_crawler.fetch_factors(...)` 中的真实 HTTP 抓取逻辑；
- BigQuant / 聚宽等其他站点的适配器模块；
- 去重规则（按 `name/raw_formula` 与现有 md 或 DB 对比跳过重复因子）；
- 原始公式向内部 DSL 的自动/半自动转写（可以结合 OpenAI/自写 parser）。

一旦这些补完，`factor_crawler` 就可以作为整个流水线的“因子入口”，自动为新来源生成标准 md 并接入后续因子计算与回测流程。

