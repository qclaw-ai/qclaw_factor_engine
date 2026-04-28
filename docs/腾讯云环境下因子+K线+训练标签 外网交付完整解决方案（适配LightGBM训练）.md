# 腾讯云环境下因子 + K线 + 训练标签 外网交付完整解决方案（修正版，适配 LightGBM 训练）

> 本文以原腾讯云交付思路为主，结合当前 `qclaw_factor_engine` 实际代码与流程修正。  
> 目标：**客户直接用**，你**单人可维护**，并且训练口径可复现。

---

# 一、方案背景与目标

## 1.1 当前工程现状（基于已有代码）

- 部署环境：腾讯云（CVM 计算、COS 对象存储）。
- 因子生产：由 `src/factor_engine/factor_engine_runner.py` 产出，按域落盘到 `factor_values/by_universe/{UNIVERSE}/`。
- 增量编排：由 `src/factor_incremental/factor_incremental_runner.py` 驱动，支持 `incremental` 与 `rebase`。
- 日更链路：由 `src/daily_factor_values/daily_factor_values_runner.py` 产出 `daily_csv`。
- 路径索引：以 `factor_value_files` 表为权威（含 `factor_id/universe/artifact_type/date_start/date_end/batch_id/stage/is_rebase`）。

## 1.2 对外交付目标

- 对外提供三类数据：`factor`、`kline`、`label`，支持历史训练与每日滚动更新。
- 客户无需理解内部拼接细节，直接调用 SDK/API 获取可训练数据。
- 数据交付安全、可追踪、可控权限。
- 保留现有因子工厂流程，不做破坏式重构。

---

# 二、核心原则（修正版）

## 2.1 五个统一

- 统一主键：`stock_code + trade_date`。
- 统一格式：Parquet（列式、压缩、可列裁剪）。
- 统一分域：`HS300/ZZ500`（后续可扩展更多 universe）。
- 统一水位：每域一个 `watermark`，固定字段 `as_of_trade_date` 标识“最新可用到哪天”。
- 统一交付形态：**对外宽表优先**（客户直接训练），内部仍保留长表真源用于追溯与重算。

## 2.2 一个关键调整

原文的 `full + daily` 结构改为：

- **数据面**：按域 + 月分区的 Parquet（不维护单一巨型 `*_full.parquet`）。
- **控制面**：通过 `manifest + watermark` 告知客户如何拉取（可用签名 URL）。

说明：这仍然是腾讯云 COS 交付，只是把“每天覆盖 full 文件”改成“分区追加 + 元数据指针”，维护更稳。

---

# 三、COS 目录结构（推荐落地）

```text
你的私有 COS 桶/
├─ factor/
│  ├─ universe=HS300/month=2026-04/
│  │  ├─ part-000.parquet
│  │  └─ part-001.parquet
│  └─ universe=ZZ500/month=2026-04/
│     └─ part-000.parquet
├─ kline/
│  ├─ universe=HS300/month=2026-04/part-000.parquet
│  └─ universe=ZZ500/month=2026-04/part-000.parquet
├─ label/
│  ├─ universe=HS300/month=2026-04/part-000.parquet
│  └─ universe=ZZ500/month=2026-04/part-000.parquet
└─ meta/
   ├─ watermark/HS300.json
   ├─ watermark/ZZ500.json
   ├─ manifest/HS300/2026-04.json
   └─ manifest/ZZ500/2026-04.json
```

---

# 四、三类数据的字段与时序定义

## 4.1 因子数据（factor）

来源：现有因子工厂输出（当前 CSV 结果可转 Parquet 后外发）。

建议字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `stock_code` | string | 股票代码，如 `000001.SZ` |
| `trade_date` | date | 交易日 |
| `factor_id` | string | 因子 ID |
| `factor_value` | float | 因子值 |

说明：外发主形态使用宽表（如 `F001/F002/...` 多列）；内部仍保留长表（`factor_id/factor_value`）作为真源，并维护宽长映射。

补充：分区按 `month`，文件内保留 `trade_date` 列，客户按日期过滤即可覆盖任意训练窗口。

## 4.2 K 线数据（kline）

建议字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `stock_code` | string | 股票代码 |
| `trade_date` | date | 交易日 |
| `open` | float | 开盘价 |
| `high` | float | 最高价 |
| `low` | float | 最低价 |
| `close` | float | 收盘价 |
| `volume` | float | 成交量 |
| `amount` | float | 成交额 |
| `turnover` | float | 换手率（可选） |

## 4.3 标签数据（label）

标签时序必须严格定义，避免前视：

- 首版唯一标签：`y_ret_1d(t) = close(t+1) / close(t) - 1`

因此：

- 在 `T` 日收盘后，**只能稳定生成特征（factor/kline）**。
- `T` 日的最终标签，需要在 `T+1` 数据到达后回填。

建议字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `stock_code` | string | 股票代码 |
| `trade_date` | date | 标签对应特征日（即 t） |
| `y_ret_1d` | float | 下一交易日收益率标签 |

---

# 五、每日自动化流水线（结合现有代码）

## 5.1 执行顺序（推荐）

1. 运行因子增量：`factor_incremental_runner`（按域执行）。
2. 产出/同步对应域 K 线增量。
3. 基于 K 线回填标签（`T` 日标签在 `T+1` 确认）。
4. 将当日新增数据写入对应月分区（factor/kline/label）（**ETL 侧实现固定使用 Polars** 做 CSV → 宽表/Parquet 写入；与《因子工厂_CSV到Parquet交付_改造清单》一致；客户只读标准 Parquet，不强制装 Polars）。
5. 生成并写入 `manifest`。
6. 更新每域 `watermark`。

## 5.2 关键对齐规则（必须）

- 外发前数据拼接以 `factor_value_files` 为主索引真源。
- 仅使用 `stage=production` 的 `batch_csv` 作为主训练来源。
- 若存在区间重叠，优先级按：
  - `is_rebase DESC`
  - `created_at DESC`
  - `id DESC`
- 对每 `(factor_id, universe)` 要先完成区间覆盖校验，再产出外发分区。

## 5.3 月增 `batch_csv` 与日更 `daily_csv` 如何配合产出对外 Parquet

与《因子值增量方案_v1》一致：**训练/历史主读 `batch_csv`（`stage=production`）**；`daily_csv` 用于**近日补数/对账**，不天然替代全历史。对外只暴露 **月分区 + 宽表 Parquet** 时，把“两路产物的配合”**收口在 ETL 内**，客户仍只拉 COS，不必自己拼 CSV。

| 来源 | `factor_value_files` | 在对外宽表中的角色 |
|------|----------------------|--------------------|
| 月增 / 大区间 batch | `artifact_type=batch_csv` | **主来源**：长历史、每月增量、rebase 纠偏后仍以本规则合并 |
| 日更 | `artifact_type=daily_csv` | **补丁**：只用于**当前自然月**内、batch 尚未覆盖到“最新几个交易日”时的补全或覆盖（见下） |

**推荐运维节奏（单人可维护）**

1. **月增跑完**（`factor_incremental_runner` 已写库、CSV 已落盘）：对该域、对该月 `YYYY-MM` 执行一次 **全月 ETL 刷新** —— 从本月的 batch 行（经 v1 区间合并、去重）pivot 成宽表，写入 `.../month=YYYY-MM/part-*.parquet`（可整月覆盖，保证幂等）。
2. **日更跑完**（`daily_factor_values_runner`）：**只 patch 当前月分区** —— 将日更中**最近 1～N 个交易日**（可配置）合并进**当月**已存在的 Parquet。  
   **冲突去重规则（已拍板）**：对同一 `universe`、同一自然月、主键 `stock_code + trade_date`（以及同一 `factor` 列若宽表为列级），**`daily_csv` 来源优先，覆盖由 `batch_csv` 合并后写入的对外宽表行**；实现上等价于后写覆盖先写。ETL 须保证幂等：同日更日重复跑，结果与一次跑一致。  
3. **rebase 批**跑完：对 `date_start~date_end` 覆盖到的**所有自然月分区**做 **重算/重写**，避免与旧分片冲突。之后若**同一月**日更仍产出同键行，**仍适用上条**（日更覆盖）。

**`watermark.json` 建议多带两项（便于对账与客服回答）**

- `batch_production_max_date`：来自主链 `batch_csv` 可解释的“批量数据已到哪一天”（可与 `as_of_trade_date` 相同或取保守值）。
- `daily_max_trade_date`：若日更进入对外层，则填日更**已并入**的最近一天；若日更**未**进对外层，可省略或与 batch 相同。

> 若日更的因子集合与月增**不完全一致**（例如日更只跑 `is_valid` 子集），必须在对外文档中说明：**外发宽表在“仅日更存在”的列上可能出现空值/缺失**，与《因子值增量方案_v1》中“计算集合与训练集合解耦”的表述一致。

---

# 六、外网交付方案（API 只做控制面，数据走 COS）

## 6.1 交付模式

- **控制面 API**（FastAPI/Flask）：
  - 鉴权（token）
  - 查询 `watermark`
  - 获取某次请求对应的 `manifest`
  - 返回 COS 预签名 URL 列表
- **数据面**：客户直接从 COS 下载 Parquet 分片（可并行）。

说明：不建议 API 直接回传大 DataFrame，吞吐与成本都更差。

## 6.2 客户侧典型调用流程

1. `get_latest(universe)` 获取最新可用日期。
2. `get_manifest(universe, start_date, end_date, factors, with_kline, with_label)` 获取文件清单。
3. 客户并行下载并读取 Parquet，按 `stock_code + trade_date` 合并训练数据。

---

# 七、SDK 使用示例（修正版）

```python
from factor_train_sdk import FactorTrainSDK
import pandas as pd
import lightgbm as lgb

sdk = FactorTrainSDK(token="your_token")

# 1) 查询水位
wm = sdk.get_latest(universe="HS300")
end_date = wm["as_of_trade_date"]

# 2) 获取数据（底层会拿 manifest + 预签名链接）
df = sdk.get_dataset(
    universe="HS300",
    start_date="2018-01-01",
    end_date=end_date,
    factors=["F001", "F002", "F003"],
    with_kline=True,
    with_label=True,
    label_name="y_ret_1d",
)

# 3) 训练（示例）
feature_cols = ["F001", "F002", "F003", "open", "high", "low", "close", "volume"]
X = df[feature_cols]
y = df["y_ret_1d"]

train_data = lgb.Dataset(X, label=y)
params = {"objective": "regression", "metric": "rmse", "learning_rate": 0.05}
model = lgb.train(params, train_set=train_data, num_boost_round=100)
model.save_model("lgb_factor_model.txt")
```

---

# 八、关键改造点（按最小成本）

## 8.1 必改项

1. 统一字段名为 `trade_date`（避免 `date`/`trade_date` 混用）。
2. 明确标签回填时序（`t` 的标签在 `t+1` 确认）。
3. 对外分区产物由 `factor_value_files + 拼接规则` 生成，禁止“仅按最新文件”直接外发。
4. 增加每域 `watermark` 与每次请求 `manifest`。

## 8.2 可选优化

1. 对高频请求因子包，做宽表热分区与缓存（首版已宽表优先，此处是性能优化）。
2. 给重要客户设置独立 token 与访问前缀，便于审计和限流。
3. 在 `meta/update_log` 记录构建 ID、覆盖区间、失败因子列表。

---

# 九、风险与避坑

1. **前视风险**：标签时序错误会直接污染训练结果。
2. **口径风险**：若 universe 成分口径与因子侧不一致，会造成回测/训练偏移。
3. **重复风险**：分区重跑时必须有幂等策略（按主键去重或全分区覆盖）。
4. **权限风险**：COS 必须私有桶，预签名短有效期，必要时加 IP 白名单。

---

# 十、落地步骤（按优先级）

1. 确认 COS 目录（分区版）与 `watermark/manifest` JSON 契约。
2. 打通因子侧外发任务：从 `factor_value_files` 读取 production 批量路径，完成 Parquet 分区产物。
3. 接入 K 线与标签（先实现 `y_ret_1d` 一种标签）。
4. 上线轻量控制面 API（latest + manifest + 预签名链接）。
5. 发布 Python SDK（先做最小 3 个接口）。
6. 灰度一个客户，验证速度、稳定性、训练复现，再扩量。

---

# 十一、与现有项目文件的对应关系

- 因子计算主入口：`src/factor_engine/factor_engine_runner.py`
- 增量编排入口：`src/factor_incremental/factor_incremental_runner.py`
- 日更入口：`src/daily_factor_values/daily_factor_values_runner.py`
- 路径索引读取：`src/common/factor_value_files_batch.py`
- 兼容旧列同步：`src/backtest_io/sync_factor_values_path_runner.py`
- 数据库约定：`docs/complete/db_conventions.md`
- 增量规则：`docs/因子值增量方案_v1.md`

---

# 十二、版本记录

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-04-27 | v2 | 以腾讯云交付方案为主，结合因子工厂现网代码修正：去除巨型 full 覆盖、补齐标签时序、引入 watermark/manifest、对齐 `factor_value_files` 规则 |
| 2026-04-27 | v2.1 | 明确：对外 Parquet 的 ETL 使用 Polars；客户侧读取任意 Parquet 工具即可 |
| 2026-04-27 | v2.2 | 新增 §5.3：月增 `batch_csv` 与日更 `daily_csv` 如何配合写月分区 Parquet 与 `watermark` 字段建议 |
| 2026-04-27 | v2.3 | §5.3 拍板：同键冲突时**日更覆盖月增（batch 合并行）** |
