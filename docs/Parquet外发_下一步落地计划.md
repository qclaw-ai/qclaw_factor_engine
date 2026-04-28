# Parquet 外发下一步落地计划（严格对齐现有三份方案文档）

> 适用范围：`qclaw_factor_engine`  
> 对齐文档：  
> - `docs/腾讯云环境下因子+K线+训练标签 外网交付完整解决方案（适配LightGBM训练）.md`  
> - `docs/因子值对外服务_架构与方案.md`  
> - `docs/因子工厂_CSV到Parquet交付_改造清单.md`  
> 目标：按优先级推进，不跳步，先把可交付链路稳定，再补完整能力。

---

## 0. 当前状态（已完成 / 未完成）

### 0.1 已完成

- `factor`：CSV -> 月分区 Parquet（`candidate`）链路可跑。  
- 已有元数据：`manifest`、`watermark`。  
- 已有校验：`scripts/validate_factor_export.py`（结构、主键、日期、CSV 抽样对账）。  
- 已有历史回填：`scripts/bootstrap_factor_export_history.py`。  
- 规则已拍板并落地：  
  - 同键冲突 `daily` 覆盖 `batch`。  
  - `watermark` 只前进不回退。

### 0.2 未完成

- `kline` 导出链路（按 `universe + month` 的 Parquet 产物）。  
- `label` 导出链路（首版 `y_ret_1d`，按标签时序回填）。  
- 控制面 API（`/latest`、`/manifest`）。  
- `candidate -> production` 发布动作与操作手册固化。

---

## 1. 第一优先级：先完成 factor 历史初始化（P0）

### 1.1 执行目标

- 对 `ZZ500`、`HS300` 两个域完成历史月份回填（例如 `2015-01` 到当前）。  
- 回填结果全部通过校验，失败月份清零。

### 1.2 执行方式

- 使用 `scripts/bootstrap_factor_export_history.py` 按月回填。  
- 每月自动执行 `scripts/validate_factor_export.py`。  
- 失败月份单独重跑，直至 0 失败。

### 1.3 验收标准

- 每个月 `part-*.parquet` 存在且可读。  
- `(stock_code, trade_date)` 无重复。  
- `trade_date` 在目标月份范围内。  
- 抽样 CSV 对账通过。  
- `watermark.as_of_trade_date` 只前进不回退。

---

## 2. 第二优先级：补齐 kline 导出链路（P1）

### 2.1 执行目标

- 按 `universe + month` 产出 K 线 Parquet：  
  - 路径：`kline/universe=.../month=YYYY-MM/part-*.parquet`  
  - 字段：`stock_code, trade_date, open, high, low, close, volume, amount, turnover`

### 2.2 核心要求

- 保持与 factor 相同分区策略（月分区）。  
- 保持与 factor 相同键（`stock_code + trade_date`）。  
- 同步生成 `manifest/watermark`（或至少纳入统一 manifest 结构）。

### 2.3 验收标准

- 字段齐全，日期范围正确。  
- 主键无重复。  
- 与源数据抽样一致。

---

## 3. 第三优先级：补齐 label 导出链路（P2）

### 3.1 执行目标

- 首版仅实现 `y_ret_1d`：  
  `y_ret_1d(t) = close(t+1) / close(t) - 1`  
- 路径：`label/universe=.../month=YYYY-MM/part-*.parquet`

### 3.2 核心要求

- 明确标签时序，避免前视：  
  - `T` 日只能稳定产特征。  
  - `T` 标签在 `T+1` 数据到达后确认。  
- `trade_date` 表示特征日期（即 t）。

### 3.3 验收标准

- 抽样算式一致。  
- 标签日期与特征日期对齐正确。  
- 月分区可读、可并行加载。

---

## 4. 第四优先级：训练就绪拼装层（P3）

### 4.1 执行目标

- 将 `factor + kline + label` 按 `stock_code + trade_date` 合并。  
- 输出客户可直接训练的宽表视图（或在 manifest 中定义联合读取方式）。

### 4.2 核心要求

- 日更覆盖规则与 factor 侧保持一致。  
- 若日更因子集小于月增全量，需明确列缺失语义。  
- 对外宽表列命名规则固定并可追溯。

---

## 5. 第五优先级：控制面 API（P4）

### 5.1 最小接口

- `GET /latest?universe=...`  
- `POST /manifest`

### 5.2 原则

- API 只做控制面（鉴权、清单、链接）。  
- 数据面由 COS 预签名链接直读（不走大数据 API 回传）。

---

## 6. 第六优先级：发布与运维固化（P5）

### 6.1 发布动作

- 明确 `candidate -> production` 发布流程（复制/同步或切换指针）。  
- 发布后更新 production 水位。

### 6.2 运维动作

- 固化定时任务顺序：factor -> kline -> label -> validate -> publish。  
- 增加失败告警与回滚步骤。  
- 补齐上线操作手册（命令可直接执行）。

---

## 7. 建议执行顺序（最小风险）

1. 先完成 `factor` 全历史回填并清零失败月。  
2. 再落 `kline` 导出。  
3. 再落 `label(y_ret_1d)` 导出。  
4. 再做训练拼装层。  
5. 最后上线控制面 API 与发布流程。

---

## 8. 里程碑定义（建议）

- **M1**：`factor` 历史回填完成（双域）+ 连续 3 天增量稳定。  
- **M2**：`kline + label` 双链路完成并通过校验。  
- **M3**：客户可通过 `latest + manifest` 拉取并直接训练。  
- **M4**：production 发布流程与回滚流程固化。

---

| 日期 | 版本 | 说明 |
|---|---|---|
| 2026-04-27 | v1 | 根据三份方案文档与当前代码状态整理的执行计划 |

