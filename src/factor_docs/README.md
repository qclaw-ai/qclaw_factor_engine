## 模块：factor_docs（因子文档解析）

> 职责：从标准化的因子 Markdown 文档中解析出结构化的因子定义对象，为后续 `factor_engine` / 回测模块提供输入。

---

### 1. 配置说明：`factor_docs/config_dev.ini`

```ini
[paths]
; 因子 Markdown 文档根目录（相对项目根路径）
factor_docs_dir = ./factor_docs/md
```

- 实际使用时，`common.Config` 会根据 `ENV` 环境变量自动选择：
  - 非 prod：`factor_docs/config_dev.ini`
  - prod：`factor_docs/config.ini`

---

### 2. 因子 Markdown 模板

每个因子一个 `*.md` 文件（推荐命名：`FACTOR_<来源缩写>_<日期>_<序号>.md`），内容示例：

```md
# 因子说明文档示例

因子ID: FACTOR_DEMO_0001  
因子名称: 20日动量因子示例  
公式(DSL): (close - REF(close, 20)) / REF(close, 20)  
描述: 用过去20个交易日的价格变化比例刻画动量强度，仅为测试用示例。  
因子类型: 动量  
适用股票池: HS300  
调仓周期: 日线  
因子方向: long  
来源URL: https://example.com/factor/demo_0001
```

字段说明（MVP 阶段）：

- **因子ID**：`factor_id`，全局唯一，后续与 DB 中 `factor_basic.factor_id` 对应。
- **因子名称**：`factor_name`，便于人工识别。
- **公式(DSL)**：`formula`，采用文档草图中定义的最小 DSL（四则运算 + `MA/REF` 等）。
- **描述**：`description`，自然语言说明。
- **因子类型**：`factor_type`，如 动量 / 价值 / 情绪 等。
- **适用股票池**：`test_universe`，如 HS300 / ALL_A 等。
- **调仓周期**：`trading_cycle`，如 日线 / 周线。
- **因子方向**：`factor_direction`，枚举 `long` / `short`。
- **来源URL**：`source_url`，原始网页或文献链接。

> 解析逻辑对字段顺序不敏感，只要包含 `因子ID: xxx` 等行即可；冒号支持中英文（`:` / `：`）。

---

### 3. 解析输出结构：`FactorDefinition`

`factor_docs_parser.py` 中定义了一个简单的数据类：

```python
FactorDefinition(
  factor_id: str,
  factor_name: str,
  formula: str,
  description: str,
  factor_type: str,
  test_universe: str,
  trading_cycle: str,
  factor_direction: str,  # 标准化为 "long" / "short"
  source_url: str,
  doc_path: str,          # 该 md 文件的绝对路径
)
```

解析规则（宽进严出）：

- 关键字段：`factor_id` / `factor_name` / `formula` / `factor_direction`
  - 若缺失 → 记录错误日志并 **跳过该文件**。
- 非关键字段：`description` / `factor_type` / `test_universe` / `trading_cycle` / `source_url`
  - 若缺失 → 使用空字符串作为默认值，并打 warning。
- 因子方向：
  - 若值为 `long` / `short`（大小写不敏感） → 直接使用；
  - 其他值 → 记录 warning，并按 `long` 处理。

---

### 4. 使用方式

在项目根目录下执行：

```bash
python factor_docs/factor_docs_parser.py
```

该脚本会：

1. 读取 `factor_docs/config_*.ini` 中的 `paths.factor_docs_dir`；
2. 递归扫描目录下所有 `.md` 文件；
3. 对每个文件调用 `parse_factor_md` 解析；
4. 最终在日志中打印出解析到的所有因子概要信息。

---

### 5. 与后续模块的衔接

- `factor_engine`：将直接使用 `FactorDefinition` 列表中的字段：
  - `formula`：用于因子计算；
  - `test_universe` / `trading_cycle` / `factor_direction`：用于决定取数范围和回测参数；
  - `factor_id`：贯穿整个流水线。
- `selection_and_store` / `factor_files`：
  - 后续会在 DB 中存储：
    - `factor_basic.factor_id` 等元信息；
    - `factor_files.doc_path` 对应本模块解析时得到的 `doc_path`。

> 当前阶段，Markdown 文档仍是“公式与元信息的唯一真源”；数据库仅存路径与元数据，具体公式暂不入库。

