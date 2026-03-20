-- PostgreSQL schema for 因子工厂 MVP
-- 说明：
-- 1. 覆盖文档《因子工厂自动化流程_MVP实现草图.md》中 3.1 / 8.1 / 9.1 的表设计；
-- 2. 仅使用通用类型 numeric / text / varchar，具体精度后续可按需要调整；
-- 3. 所有时间字段默认使用 CURRENT_TIMESTAMP 便于快速落地。

BEGIN;

-- =========================================
-- 1. 行情表：stock_daily（A 股日行情）
-- =========================================

CREATE TABLE IF NOT EXISTS stock_daily (
    stock_code   varchar(32) NOT NULL,          -- 股票代码
    trade_date   date        NOT NULL,          -- 交易日期
    open         numeric,                       -- 开盘价
    high         numeric,                       -- 最高价
    low          numeric,                       -- 最低价
    close        numeric,                       -- 收盘价
    volume       numeric,                       -- 成交量
    turnover     numeric,                       -- 成交额
    pre_close    numeric,                       -- 前收盘价
    high_limit   numeric,                       -- 涨停价
    low_limit    numeric,                       -- 跌停价
    "return"     numeric,                       -- 日收益（万分）
    is_suspend   boolean,                      -- 是否停牌
    multiple     integer,                      -- 期货合约乘数（股票=1）
    update_time  timestamp,                    -- 更新时间
    PRIMARY KEY (trade_date, stock_code)        -- 复合主键，对应 (trade_date, stock_code) 复合索引
);

-- 如需按股票代码维度加速，可额外建索引：
CREATE INDEX IF NOT EXISTS idx_stock_daily_stock_code_trade_date
    ON stock_daily (stock_code, trade_date);


-- =========================================
-- 2. 因子基础信息表：factor_basic
-- =========================================

CREATE TABLE IF NOT EXISTS factor_basic (
    factor_id         varchar(128) PRIMARY KEY,      -- 因子ID，来自文档 & md
    factor_name       varchar(256) NOT NULL,         -- 因子名称
    factor_type       varchar(64),                   -- 因子类型（动量/量价等）
    test_universe     varchar(64),                   -- 测试股票池（全A/沪深300等）
    trading_cycle     varchar(32),                   -- 调仓/交易周期（如 日线）
    source_url        varchar(512),                  -- 来源链接
    create_time       timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 入库时间
    is_valid          boolean   NOT NULL DEFAULT TRUE,              -- 当前是否有效
    deprecate_reason  varchar(128),                  -- 淘汰原因（performance/bug/data_issue 等）
    deprecate_time    timestamp,                     -- 最近一次被标记为无效的时间
    reactivated_time  timestamp                      -- 最近一次被重新启用的时间（可选）
);


-- =========================================
-- 3. 因子回测记录表：factor_backtest
-- =========================================

CREATE TABLE IF NOT EXISTS factor_backtest (
    id                 serial PRIMARY KEY,           -- 主键
    factor_id          varchar(128) NOT NULL,        -- 因子ID，外键到 factor_basic
    backtest_period    varchar(128) NOT NULL,        -- 回测区间（如 "2021-01-01 至 2025-12-31"）
    horizon            varchar(32)  NOT NULL,        -- 收益 horizon（如 "5d"）
    ic_value           numeric,                      -- IC
    ic_ir              numeric,                      -- IC_IR
    sharpe_ratio       numeric,                      -- 夏普比率
    max_drawdown       numeric,                      -- 最大回撤
    turnover           numeric,                      -- 换手率
    pass_standard      boolean,                      -- 是否通过当前阈值标准
    backtest_time      timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP, -- 回测时间
    comment            text                          -- 可选：用于标记 initial/recheck/reactivate 等信息
);

ALTER TABLE factor_backtest
    ADD CONSTRAINT fk_factor_backtest_factor
    FOREIGN KEY (factor_id) REFERENCES factor_basic (factor_id)
    ON UPDATE CASCADE
    ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_factor_backtest_factor_id
    ON factor_backtest (factor_id);


-- =========================================
-- 4. 因子文件路径表：factor_files
-- =========================================

CREATE TABLE IF NOT EXISTS factor_files (
    factor_id           varchar(128) PRIMARY KEY,    -- 因子ID，对应 factor_basic.factor_id
    doc_path            varchar(1024) NOT NULL,      -- 因子 md 文件路径
    backtest_json_path  varchar(1024),               -- 最新一次回测 JSON 路径
    log_path            varchar(1024)                -- 回测日志路径
);

ALTER TABLE factor_files
    ADD CONSTRAINT fk_factor_files_factor
    FOREIGN KEY (factor_id) REFERENCES factor_basic (factor_id)
    ON UPDATE CASCADE
    ON DELETE CASCADE;


-- =========================================
-- 5. 阈值配置表：factor_threshold_config
-- =========================================

CREATE TABLE IF NOT EXISTS factor_threshold_config (
    id                      serial PRIMARY KEY,      -- 主键
    scene                   varchar(128) NOT NULL,   -- 使用场景（如 "A_stock_daily_single_factor"）
    version                 varchar(32)  NOT NULL,   -- 配置版本（如 "v1"）
    ic_min                  numeric,                 -- 入库：IC 最小值
    ic_ir_min               numeric,                 -- 入库：IC_IR 最小值
    sharpe_min              numeric,                 -- 入库：夏普最小值
    max_drawdown_max        numeric,                 -- 入库：最大回撤上限
    turnover_max            numeric,                 -- 入库：换手率上限
    -- 复检/复活等场景的预留字段（可选，用于 8.1 中的设计）
    ic_decay_threshold      numeric,                 -- 过时判定：IC 衰减比例阈值
    latest_ic_min           numeric,                 -- 过时判定：最新 IC 下限
    ic_min_reactivate       numeric,                 -- 复活：IC 下限
    ic_ir_min_reactivate    numeric,                 -- 复活：IC_IR 下限
    sharpe_min_reactivate   numeric,                 -- 复活：夏普下限
    max_drawdown_max_reactivate numeric,             -- 复活：最大回撤上限
    is_active               boolean   NOT NULL DEFAULT FALSE,       -- 是否当前生效版本
    created_at              timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    comment                 text                     -- 备注
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_threshold_scene_version
    ON factor_threshold_config (scene, version);

CREATE INDEX IF NOT EXISTS idx_factor_threshold_scene_active
    ON factor_threshold_config (scene, is_active);


-- =========================================
-- 6. 因子候选表：factor_candidate（9.1，建议实现）
-- =========================================

CREATE TABLE IF NOT EXISTS factor_candidate (
    candidate_id         serial PRIMARY KEY,         -- 候选因子主键
    status               varchar(32)  NOT NULL,      -- submitted/parsed/ready_for_backtest/rejected
    source_type          varchar(32)  NOT NULL,      -- website/paper/report/other
    source_ref           text        NOT NULL,       -- 来源引用（URL/DOI/文件路径等）
    raw_title            text,                       -- 原始标题
    raw_description      text,                       -- 自然语言描述
    raw_formula_snippet  text,                       -- 原始公式/代码片段
    suggested_universe   varchar(64),                -- 建议股票池
    suggested_period     varchar(32),                -- 建议调仓周期（日/周/月）
    suggested_type       varchar(64),                -- 建议因子类型（动量/价值等）
    linked_factor_id     varchar(128),               -- 生成的正式因子ID（如有）
    created_by           varchar(128),               -- 提交人
    created_at           timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reject_reason        text                        -- 若 status=rejected，记录原因
);

CREATE INDEX IF NOT EXISTS idx_factor_candidate_status
    ON factor_candidate (status);

CREATE INDEX IF NOT EXISTS idx_factor_candidate_linked_factor_id
    ON factor_candidate (linked_factor_id);


-- =========================================
-- 7. 预留（非 MVP 必需）：factor_definition 表（7.1）
--    如需启用“公式入库 + 版本管理”，可取消注释本段。
-- =========================================

-- CREATE TABLE IF NOT EXISTS factor_definition (
--     id               serial PRIMARY KEY,          -- 主键
--     factor_id        varchar(128) NOT NULL,       -- 因子ID，外键指向 factor_basic
--     dsl_expr         text        NOT NULL,        -- 因子 DSL 公式
--     data_domain      varchar(64),                 -- 数据域（price/fundamental 等）
--     preprocess_config jsonb,                      -- 预处理配置（如 winsorize / z-score 等）
--     default_horizon  varchar(32),                 -- 默认 horizon（如 "5d"）
--     version          varchar(32)  NOT NULL,       -- 定义版本（如 "v1"）
--     is_active        boolean     NOT NULL DEFAULT TRUE, -- 是否当前使用版本
--     created_at       timestamp   NOT NULL DEFAULT CURRENT_TIMESTAMP,
--     comment          text                            -- 备注
-- );
--
-- ALTER TABLE factor_definition
--     ADD CONSTRAINT fk_factor_definition_factor
--     FOREIGN KEY (factor_id) REFERENCES factor_basic (factor_id)
--     ON UPDATE CASCADE
--     ON DELETE CASCADE;
--
-- CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_definition_factor_version
--     ON factor_definition (factor_id, version);
--
-- CREATE INDEX IF NOT EXISTS idx_factor_definition_factor_active
--     ON factor_definition (factor_id, is_active);


COMMIT;

