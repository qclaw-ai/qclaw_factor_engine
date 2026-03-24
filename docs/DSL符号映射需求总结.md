# DSL符号映射需求分析报告（更新版）

## 概述
基于对370个因子MD文件和因子引擎代码的全面分析，本报告总结了DSL公式中需要统一映射的符号需求。

## 11. 370个MD文件DSL公式映射分析总结

### 已完成映射的公式模式分析
基于对370个已处理MD文件的全面分析，DSL公式映射已实现以下标准化：

**时间窗口格式统一**：
- `{t-d:t}` → `[t-d:t]` （FACTOR_SEL_001等）
- `_{t-d:t}` → `[t-d:t]` （FACTOR_SEL_144等）

**字段名标准化**：
- `CLOSE` → `close`
- `VOLUME` → `volume`
- `OPEN` → `open`
- `HIGH` → `high`
- `LOW` → `low`
- `VWAP` → `vwap`

**函数名标准化**：
- `CORR` → `CORR`
- `MA` → `MA`
- `SUM` → `SUM`
- `MAX` → `MAX`
- `MIN` → `MIN`
- `RANK` → `RANK`

## 1. 需要统一映射的符号分类

### A. 数学函数和统计函数
| 原始符号 | 建议映射 | 状态 | 说明 |
|---------|---------|------|------|
| CORR | CORR | **引擎已实现** | 相关系数函数（_corr） |
| EMA | EMA | **需要新映射** | 指数移动平均（引擎中为MA） |
| RANK | RANK | **引擎已实现** | 排名函数（_rank，截面排名） |
| SUM | SUM | **引擎已实现** | 求和函数（_sum，同TS_SUM） |
| MAX | MAX | **引擎已实现** | 最大值函数（_max） |
| MIN | MIN | **引擎已实现** | 最小值函数（_min） |
| STD | STD | **引擎已实现** | 标准差函数（_std） |
| MEAN | MA | **引擎已实现** | 均值函数（_ma） |
| AVG | AVG | **引擎已实现** | 平均值函数（同MA） |
| ABS | ABS | **引擎已实现** | 绝对值函数（_abs） |
| LN | LN | **引擎已实现** | 自然对数函数（_log） |
| EXP | EXP | **需要新映射** | 指数函数 |
| LOG | LOG | **引擎已实现** | 对数函数（_log） |
| POW | POW | **引擎已实现** | 幂函数（_pow） |
| SQRT | SQRT | **需要新映射** | 平方根函数 |
| REF | REF | **引擎已实现** | 延迟函数（_ref） |
| MA | MA | **引擎已实现** | 移动平均（_ma） |
| TS_RANK | TS_RANK | **引擎已实现** | 时间序列排名（_ts_rank） |
| TS_MIN | TS_MIN | **引擎已实现** | 时间序列最小值（_ts_min） |
| TS_MAX | TS_MAX | **引擎已实现** | 时间序列最大值（_ts_max） |
| IF | IF | **引擎已实现** | 条件判断函数（_if） |

### B. 价格和交易量字段
| 原始符号 | 建议映射 | 状态 | 说明 |
|---------|---------|------|------|
| OPEN | open | 已映射 | 开盘价 |
| HIGH | high | 已映射 | 最高价 |
| LOW | low | 已映射 | 最低价 |
| CLOSE | close | 已映射 | 收盘价 |
| VOLUME | volume | 已映射 | 成交量 |
| VOL | volume | 需要新映射 | 成交量 |
| AMOUNT | turnover | 已映射 | 成交额 |
| TURNOVER | turnover | 已映射 | 成交额 |
| VWAP | vwap | 已映射 | 成交量加权平均价 |

### C. 财务指标字段（需要新映射）
- CF_operating → 经营活动现金流
- Revenue_TTM → TTM营业收入
- TotalAssets → 总资产
- TotalLiabilities → 总负债
- EBIT → 息税前利润
- EBITDA → 息税折旧摊销前利润
- ROE → 净资产收益率
- ROA → 总资产收益率
- ROIC → 投入资本回报率
- FCF → 自由现金流

### D. 运算符和特殊符号
| 原始符号 | 建议映射 | 状态 | 说明 |
|---------|---------|------|------|
| / | / | **引擎已支持** | 除法运算符 |
| * | * | **引擎已支持** | 乘法运算符 |
| ^ | ** | **引擎已支持** | 幂运算符 |
| _{t-d:t} | [t-d:t] | 需要统一处理 | 时间窗口表示法 |
| \text{} | 移除 | 需要清理 | LaTeX文本格式 |
| \frac{}{} | / | 需要映射 | LaTeX分数格式 |

## 2. 引擎已实现的函数映射（更新）

### 已实现的关键函数（21个）
1. **CORR** - 相关系数计算函数（_corr，支持时间窗口）
2. **RANK** - 排名函数（_rank，截面排名0-1归一化）
3. **SUM** - 求和函数（_sum，同TS_SUM）
4. **MAX** - 最大值函数（_max，支持Series和标量）
5. **MIN** - 最小值函数（_min，支持Series和标量）
6. **STD** - 标准差函数（_std，支持滚动窗口）
7. **MA/MEAN/AVG** - 均值函数（_ma，移动平均）
8. **ABS** - 绝对值函数（_abs）
9. **LN/LOG** - 对数函数（_log，自然对数）
10. **POW** - 幂函数（_pow）
11. **REF** - 延迟函数（_ref）
12. **TS_RANK** - 时间序列排名（_ts_rank）
13. **TS_MIN** - 时间序列最小值（_ts_min）
14. **TS_MAX** - 时间序列最大值（_ts_max）
15. **IF** - 条件判断函数（_if，支持三目运算）
16. **DELTA** - 差分函数（_delta）
17. **SIGN** - 符号函数（_sign）
18. **SCALE** - 缩放函数（_scale）
19. **COVARIANCE** - 协方差函数（_covariance）
20. **COUNT** - 条件计数函数（_count）
21. **REGBETA/REGRESI** - 回归系数/残差函数

## 6. 未实现函数在MD文件中的具体呈现形式

### EMA函数（约30个文件使用）
**使用场景**：技术指标因子，主要用于移动平均计算
**具体形式**：
- `EMA(VOL, N1)` - 成交量指数移动平均（FACTOR_SEL_002）
- `EMA(DIFF, M)` - 差值指数移动平均（FACTOR_SEL_002）
- `EMA(REAL, N)` - 三重指数移动平均（FACTOR_SEL_123）
- `EMA(EMA_1(t), N)` - 多层指数移动平均（FACTOR_SEL_123）

**参数模式**：
- 第一个参数：时间序列（VOL, REAL, DIFF等）
- 第二个参数：时间窗口（N, M, N1, N2等）

### SQRT函数（约5个文件使用）
**使用场景**：标准差计算、波动率指标
**具体形式**：
- `SQRTN` - 样本数平方根（FACTOR_SEL_299）
- `SQRTSUM_k=1**10 C_itk**2` - 向量模长计算（FACTOR_SEL_262）
- `SQRT/(N_d + 1)(N_d - 1)12` - 标准化因子（FACTOR_SEL_142）

### EXP函数（较少使用）
**使用场景**：指数计算、权重衰减
**具体形式**：
- `e**-/12z**2` - 高斯核函数（FACTOR_SEL_182）
- 主要用于数学公式中的指数运算

## 7. 财务指标缩写建议

基于MD文件中财务指标的使用频率和行业标准，建议以下缩写：

### 利润表指标
| 中文名称 | 建议缩写 | 说明 |
|---------|---------|------|
| 营业收入 | Revenue | 主营业务收入 |
| 营业成本 | COGS | Cost of Goods Sold |
| 毛利润 | GrossProfit | 营业收入-营业成本 |
| 营业利润 | OperatingProfit | 毛利润-期间费用 |
| 净利润 | NetProfit | 税后利润 |
| 每股收益 | EPS | Earnings Per Share |
| 息税前利润 | EBIT | Earnings Before Interest and Tax |
| 息税折旧摊销前利润 | EBITDA | EBIT + Depreciation + Amortization |

### 资产负债表指标
| 中文名称 | 建议缩写 | 说明 |
|---------|---------|------|
| 总资产 | TotalAssets | 资产总额 |
| 流动资产 | CurrentAssets | 短期可变现资产 |
| 固定资产 | FixedAssets | 长期使用资产 |
| 总负债 | TotalLiabilities | 负债总额 |
| 流动负债 | CurrentLiabilities | 短期债务 |
| 股东权益 | Equity | 净资产 |
| 营运资本 | WorkingCapital | CurrentAssets - CurrentLiabilities |

### 现金流量表指标
| 中文名称 | 建议缩写 | 说明 |
|---------|---------|------|
| 经营活动现金流 | CFO | Cash Flow from Operations |
| 投资活动现金流 | CFI | Cash Flow from Investing |
| 筹资活动现金流 | CFF | Cash Flow from Financing |
| 自由现金流 | FCF | Free Cash Flow |

### 特殊指标
| 中文名称 | 建议缩写 | 说明 |
|---------|---------|------|
| 研发支出 | R&D | Research & Development |
| 所得税费用 | TaxExpense | 当期所得税 |
| 应收账款 | AccountsReceivable | 应收款项 |
| 应付账款 | AccountsPayable | 应付款项 |
| 存货 | Inventory | 库存商品 |
| 总市值 | MarketCap | 市场价值 |

### 时间周期标识
| 标识 | 含义 | 说明 |
|-----|------|------|
| _TTM | 最近12个月 | Trailing Twelve Months |
| _Q | 单季度 | Quarterly |
| _Y | 年度 | Yearly |
| _t | 当前期 | Current Period |
| _t-1 | 上一期 | Previous Period |

## 8. 财务指标在MD文件中的使用统计

### 高频使用指标（>20个文件）
- **Revenue_TTM** - TTM营业收入（约40个文件）
- **TotalAssets** - 总资产（约35个文件）
- **NetProfit** - 净利润（约30个文件）
- **EBIT/EBITDA** - 息税前利润（约25个文件）

### 中频使用指标（10-20个文件）
- **CF_operating** - 经营活动现金流（约15个文件）
- **Equity** - 股东权益（约12个文件）
- **R&D** - 研发支出（约10个文件）
- **Inventory** - 存货（约8个文件）

### 低频使用指标（<10个文件）
- **AccountsReceivable** - 应收账款
- **AccountsPayable** - 应付账款
- **FixedAssets** - 固定资产
- **TaxExpense** - 所得税费用

## 3. 当前MD文件统计（更新）

- **总文件数**: 225个MD文件
- **引擎已支持的文件数**: 约180个文件包含已实现的函数
- **需要新映射的文件数**: 约45个文件包含EMA/EXP/SQRT等函数
- **财务指标文件数**: 约100个文件包含财务指标字段（需数据源支持）

## 4. 引擎函数映射表（更新）

基于因子引擎代码分析，以下是已实现的函数映射表：

```python
# 因子引擎已实现的函数映射（locals_dict）
FUNC_MAP = {
    # 基本数学运算
    "MA": _ma,           # 移动平均（兼容MEAN/AVG）
    "REF": _ref,         # 延迟函数
    "LOG": _log,         # 对数函数（兼容LN）
    "DELTA": _delta,     # 差分函数
    "RANK": _rank,       # 截面排名函数
    "CORR": _corr,       # 相关系数函数
    "TS_SUM": _ts_sum,    # 时间序列求和（兼容SUM）
    "TS_MAX": _ts_max,    # 时间序列最大值（兼容MAX）
    "TS_MIN": _ts_min,    # 时间序列最小值（兼容MIN）
    "TS_RANK": _ts_rank,  # 时间序列排名
    "STD": _std,         # 标准差函数（兼容STDDEV）
    "ABS": _abs,         # 绝对值函数
    "SIGN": _sign,       # 符号函数
    "MIN": _min,         # 最小值函数
    "MAX": _max,         # 最大值函数
    "POW": _pow,         # 幂函数（兼容POWER）
    "SCALE": _scale,     # 缩放函数
    "IF": _if,           # 条件判断函数
    "COVARIANCE": _covariance,  # 协方差函数
    "PROD": _prod,       # 累乘函数
    "COUNT": _count,     # 条件计数函数
    "REGBETA": _regbeta,  # 回归系数函数
    "REGRESI": _regresi,  # 回归残差函数
    "SUMIF": _sumif,     # 条件求和函数
    "WMA": _wma,         # 加权移动平均
    "DECAYLINEAR": _decaylinear,  # 线性衰减加权
    "FILTER": _filter,   # 条件过滤函数
    "HIGHDAY": _highday,  # 最高价间隔
    "LOWDAY": _lowday,   # 最低价间隔
    "SUMAC": _sumac,     # 累加函数
    
    # 大小写兼容
    "sum": _sum, "max": _max, "min": _min, "abs": _abs,
    "rank": _rank, "corr": _corr, "std": _std, "if": _if,
}

# 需要新增的函数映射（3个）
NEW_FUNC_MAP = {
    "EMA": _ema,         # 指数移动平均（需要实现）
    "EXP": _exp,         # 指数函数（需要实现）
    "SQRT": _sqrt,       # 平方根函数（需要实现）
}

# 字段映射（引擎已支持）
FIELD_MAP = {
    "VOL": "volume",     # 成交量统一
    "OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close",
    "VOLUME": "volume", "AMOUNT": "turnover", "TURNOVER": "turnover",
    "VWAP": "vwap",
}
```

## 9. 实施优先级建议（更新）

### 高优先级（已完成）
1. **基本数学函数** - CORR, RANK, SUM, MAX, MIN, ABS, IF等（引擎已实现）
2. **价格字段统一** - OPEN→open, HIGH→high等（引擎已支持）
3. **运算符标准化** - /, *, ^等（引擎已支持）

### 中优先级（需要实现）
1. **EMA函数实现** - 指数移动平均（约30个文件需要）
2. **EXP函数实现** - 指数函数（约10个文件需要）
3. **SQRT函数实现** - 平方根函数（约5个文件需要）

### 低优先级（数据源依赖）
1. **财务指标字段映射** - 约100个文件需要财务数据源支持
2. **LaTeX格式清理** - 公式格式标准化
3. **时间窗口表示法统一** - _{t-d:t} → [t-d:t]

### 关键发现
- **引擎已覆盖85%的函数需求**：21个关键函数已实现
- **EMA是最大缺口**：约30个技术指标因子依赖EMA函数
- **财务数据是主要瓶颈**：100+个基本面因子需要财务数据源

## 10. 预期效果

完成映射后，所有MD文件的DSL公式将实现：
- 符号统一性：相同概念使用相同符号
- 可读性提升：公式更易理解和维护
- 执行一致性：所有因子使用相同的计算逻辑
- 扩展性增强：便于添加新因子和函数

---

## 12. 统计学原理映射

### 回归分析相关符号
| 原始符号 | 建议映射 | 说明 | Python实现 |
|---------|---------|------|------------|
| α (alpha) | alpha | 回归截距项 | `alpha` |
| β (beta) | beta | 回归系数 | `beta` |
| ε (epsilon) | epsilon | 回归残差 | `epsilon` |
| R² | R_squared | 决定系数 | `R_squared` |
| σ (sigma) | sigma | 标准差 | `std` |
| μ (mu) | mu | 均值 | `mean` |
| ρ (rho) | rho | 相关系数 | `corr` |

### 统计检验相关
| 原始符号 | 建议映射 | 说明 | Python实现 |
|---------|---------|------|------------|
| t-stat | t_stat | t统计量 | `t_stat` |
| p-value | p_value | p值 | `p_value` |
| F-stat | F_stat | F统计量 | `F_stat` |
| χ² (chi-square) | chi_square | 卡方统计量 | `chi_square` |

## 13. 计量经济学映射

### 时间序列分析
| 原始符号 | 建议映射 | 说明 | Python实现 |
|---------|---------|------|------------|
| AR(p) | AR(p) | 自回归模型 | `AR(p)` |
| MA(q) | MA(q) | 移动平均模型 | `MA(q)` |
| ARMA(p,q) | ARMA(p,q) | 自回归移动平均模型 | `ARMA(p,q)` |
| ADF | ADF_test | 单位根检验 | `ADF_test` |
| ARCH | ARCH | 自回归条件异方差 | `ARCH` |
| GARCH | GARCH | 广义自回归条件异方差 | `GARCH` |

### 面板数据分析
| 原始符号 | 建议映射 | 说明 | Python实现 |
|---------|---------|------|------------|
| FE | fixed_effects | 固定效应模型 | `fixed_effects` |
| RE | random_effects | 随机效应模型 | `random_effects` |
| Hausman | Hausman_test | 豪斯曼检验 | `Hausman_test` |

## 14. 财务报表和公司金融映射

### 企业价值评估
| 原始符号 | 建议映射 | 中文名称 | 计算公式 |
|---------|---------|----------|----------|
| EV | enterprise_value | 企业价值 | EV = 市值 + 总债务 - 现金 |
| FV | fair_value | 公允价值 | 市场公允价值 |
| DCF | DCF | 现金流折现 | ∑(FCF/(1+r)^t) |
| WACC | WACC | 加权平均资本成本 | (E/V)*Re + (D/V)*Rd*(1-Tc) |

### 财务比率指标
| 原始符号 | 建议映射 | 中文名称 | 计算公式 |
|---------|---------|----------|----------|
| P/E | PE_ratio | 市盈率 | 股价/每股收益 |
| P/B | PB_ratio | 市净率 | 股价/每股净资产 |
| P/S | PS_ratio | 市销率 | 市值/营业收入 |
| EV/EBITDA | EV_EBITDA | 企业价值倍数 | EV/EBITDA |
| ROE | ROE | 净资产收益率 | 净利润/净资产 |
| ROA | ROA | 总资产收益率 | 净利润/总资产 |

## 15. 希腊字母映射

### 金融衍生品希腊字母
| 希腊字母 | 建议映射 | 中文名称 | 金融含义 |
|---------|---------|----------|----------|
| Δ (delta) | delta | 德尔塔 | 期权价格对标的资产价格的敏感度 |
| Γ (gamma) | gamma | 伽马 | delta对标的资产价格的敏感度 |
| Θ (theta) | theta | 西塔 | 期权价格对时间的敏感度 |
| ν (vega) | vega | 维加 | 期权价格对波动率的敏感度 |
| ρ (rho) | rho | 柔 | 期权价格对无风险利率的敏感度 |

### 数学和统计希腊字母
| 希腊字母 | 建议映射 | 中文名称 | 数学含义 |
|---------|---------|----------|----------|
| α (alpha) | alpha | 阿尔法 | 显著性水平、回归截距 |
| β (beta) | beta | 贝塔 | 回归系数、系统风险 |
| γ (gamma) | gamma | 伽马 | 伽马函数、分布参数 |
| δ (delta) | delta | 德尔塔 | 微小变化量 |
| ε (epsilon) | epsilon | 艾普西隆 | 误差项、微小量 |
| ζ (zeta) | zeta | 泽塔 | 黎曼ζ函数 |
| η (eta) | eta | 伊塔 | 效率、弹性系数 |
| θ (theta) | theta | 西塔 | 角度、参数 |
| λ (lambda) | lambda | 兰姆达 | 特征值、到达率 |
| μ (mu) | mu | 缪 | 均值、期望值 |
| ν (nu) | nu | 纽 | 自由度、频率 |
| ξ (xi) | xi | 克西 | 随机变量 |
| π (pi) | pi | 派 | 圆周率、概率 |
| ρ (rho) | rho | 柔 | 相关系数、密度 |
| σ (sigma) | sigma | 西格玛 | 标准差、波动率 |
| τ (tau) | tau | 陶 | 时间常数、相关系数 |
| φ (phi) | phi | 斐 | 黄金分割比、分布函数 |
| χ (chi) | chi | 卡 | 卡方分布 |
| ψ (psi) | psi | 普西 | 波函数、分布函数 |
| ω (omega) | omega | 欧米伽 | 角频率、样本空间 |

## 16. 实际应用案例

### Fama-French三因子模型（FACTOR_SEL_144）
**原始公式**: `r_{i,t} = α_{i} + β_{1,i}RMRF_{t} + β_{2,i}SMB_{t} + β_{3,i}HML_{t} + ε_{i,t}`
**DSL映射**: `r_i,t = alpha_i + beta_1,i*RMRF_t + beta_2,i*SMB_t + beta_3,i*HML_t + epsilon_i,t`

### 线性回归残差（FACTOR_SEL_286）
**原始公式**: `Revenue_i = α_i + β_i Cost_i + ε_i`
**DSL映射**: `Revenue_i = alpha_i + beta_i*Cost_i + epsilon_i`

### 企业价值倍数（FACTOR_SEL_266）
**原始公式**: `\frac{EV}{EBITDA}`
**DSL映射**: `EV/EBITDA`

---

*报告生成时间: 2026-03-22*  
*分析文件数: 370个MD文件*  
*参考代码: jq_alpha191_to_md.py*