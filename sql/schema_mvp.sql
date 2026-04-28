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

CREATE TABLE public.factor_basic (
	factor_id varchar(128) NOT NULL,
	factor_name varchar(256) NOT NULL,
	factor_type varchar(64) NULL,
	test_universe varchar(64) NULL,
	trading_cycle varchar(32) NULL,
	source_url varchar(512) NULL,
	create_time timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
	is_valid bool DEFAULT true NOT NULL,
	deprecate_reason varchar(128) NULL,
	deprecate_time timestamp NULL,
	reactivated_time timestamp NULL,
	CONSTRAINT factor_basic_pkey PRIMARY KEY (factor_id)
);


-- =========================================
-- 3. 因子回测记录表：factor_backtest
-- =========================================

CREATE TABLE public.factor_backtest (
	id serial4 NOT NULL,
	factor_id varchar(128) NOT NULL,
	backtest_period varchar(128) NOT NULL,
	horizon varchar(32) NOT NULL,
	ic_value numeric NULL,
	ic_ir numeric NULL,
	sharpe_ratio numeric NULL,
	max_drawdown numeric NULL,
	turnover numeric NULL,
	pass_standard bool NULL,
	backtest_time timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
	"comment" text NULL,
	test_universe varchar(64) NOT NULL,
	result_json_rel_path varchar(1024) NULL,
	CONSTRAINT factor_backtest_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_factor_backtest_factor_id ON public.factor_backtest USING btree (factor_id);
CREATE INDEX idx_factor_backtest_factor_universe_time ON public.factor_backtest USING btree (factor_id, test_universe, backtest_time DESC);


-- public.factor_backtest foreign keys

ALTER TABLE public.factor_backtest ADD CONSTRAINT fk_factor_backtest_factor FOREIGN KEY (factor_id) REFERENCES public.factor_basic(factor_id) ON DELETE RESTRICT ON UPDATE CASCADE;

-- =========================================
-- 3b. 因子-领域有效状态：factor_universe_status
-- =========================================

CREATE TABLE public.factor_universe_status (
	factor_id varchar(128) NOT NULL,
	test_universe varchar(64) NOT NULL,
	is_valid bool DEFAULT false NOT NULL,
	updated_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
	CONSTRAINT factor_universe_status_pkey PRIMARY KEY (factor_id, test_universe)
);


-- public.factor_universe_status foreign keys

ALTER TABLE public.factor_universe_status ADD CONSTRAINT fk_factor_universe_status_factor FOREIGN KEY (factor_id) REFERENCES public.factor_basic(factor_id) ON DELETE CASCADE ON UPDATE CASCADE;


-- =========================================
-- 4. 因子文件路径表：factor_files
-- =========================================

CREATE TABLE public.factor_files (
	factor_id varchar(128) NOT NULL,
	doc_path varchar(1024) NULL,
	log_path varchar(1024) NULL,
	CONSTRAINT factor_files_pkey PRIMARY KEY (factor_id)
);


-- public.factor_files foreign keys

ALTER TABLE public.factor_files ADD CONSTRAINT fk_factor_files_factor FOREIGN KEY (factor_id) REFERENCES public.factor_basic(factor_id) ON DELETE CASCADE ON UPDATE CASCADE;


-- =========================================
-- 4b. 因子值路径表：factor_value_files（真分域）
-- =========================================

CREATE TABLE public.factor_value_files (
	id serial4 NOT NULL,
	factor_id varchar(128) NOT NULL,
	universe varchar(64) NOT NULL,
	artifact_type varchar(32) NOT NULL,
	rel_path varchar(1024) NOT NULL,
	date_start date NULL,
	date_end date NULL,
	trade_date date NULL,
	created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
	"comment" text NULL,
	batch_id varchar(128) NULL,
	stage varchar(16) DEFAULT 'candidate'::character varying NOT NULL,
	is_rebase bool DEFAULT false NOT NULL,
	CONSTRAINT ck_factor_value_files_artifact_type CHECK (((artifact_type)::text = ANY ((ARRAY['batch_csv'::character varying, 'daily_csv'::character varying])::text[]))),
	CONSTRAINT ck_factor_value_files_batch_fields CHECK ((((artifact_type)::text <> 'batch_csv'::text) OR ((date_start IS NOT NULL) AND (date_end IS NOT NULL) AND (trade_date IS NULL)))),
	CONSTRAINT ck_factor_value_files_daily_fields CHECK ((((artifact_type)::text <> 'daily_csv'::text) OR ((trade_date IS NOT NULL) AND (date_start IS NULL) AND (date_end IS NULL)))),
	CONSTRAINT ck_factor_value_files_stage CHECK (((stage)::text = ANY ((ARRAY['candidate'::character varying, 'production'::character varying, 'deprecated'::character varying])::text[]))),
	CONSTRAINT factor_value_files_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_factor_value_files_batch_stage_cov ON public.factor_value_files USING btree (factor_id, universe, artifact_type, stage, is_rebase, date_start, date_end, created_at DESC) WHERE ((artifact_type)::text = 'batch_csv'::text);
CREATE INDEX idx_factor_value_files_factor_universe_type ON public.factor_value_files USING btree (factor_id, universe, artifact_type, created_at DESC);
CREATE INDEX idx_fvf_train_merge_lookup ON public.factor_value_files USING btree (factor_id, universe, artifact_type, is_rebase, created_at DESC, id DESC, date_start, date_end) WHERE ((artifact_type)::text = 'batch_csv'::text);
CREATE UNIQUE INDEX uq_factor_value_files_batch ON public.factor_value_files USING btree (factor_id, universe, artifact_type, date_start, date_end) WHERE ((artifact_type)::text = 'batch_csv'::text);
CREATE UNIQUE INDEX uq_factor_value_files_daily ON public.factor_value_files USING btree (factor_id, universe, artifact_type, trade_date) WHERE ((artifact_type)::text = 'daily_csv'::text);


-- public.factor_value_files foreign keys

ALTER TABLE public.factor_value_files ADD CONSTRAINT fk_factor_value_files_factor FOREIGN KEY (factor_id) REFERENCES public.factor_basic(factor_id) ON DELETE CASCADE ON UPDATE CASCADE;

-- =========================================
-- 5. 阈值配置表：factor_threshold_config
-- =========================================

CREATE TABLE public.factor_threshold_config (
	id serial4 NOT NULL,
	scene varchar(128) NOT NULL,
	"version" varchar(32) NOT NULL,
	ic_min numeric NULL,
	ic_ir_min numeric NULL,
	sharpe_min numeric NULL,
	max_drawdown_max numeric NULL,
	turnover_max numeric NULL,
	ic_decay_threshold numeric NULL,
	latest_ic_min numeric NULL,
	ic_min_reactivate numeric NULL,
	ic_ir_min_reactivate numeric NULL,
	sharpe_min_reactivate numeric NULL,
	max_drawdown_max_reactivate numeric NULL,
	is_active bool DEFAULT false NOT NULL,
	created_at timestamp DEFAULT CURRENT_TIMESTAMP NOT NULL,
	"comment" text NULL,
	CONSTRAINT factor_threshold_config_pkey PRIMARY KEY (id)
);
CREATE INDEX idx_factor_threshold_scene_active ON public.factor_threshold_config USING btree (scene, is_active);
CREATE UNIQUE INDEX uq_factor_threshold_scene_version ON public.factor_threshold_config USING btree (scene, version);


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


CREATE TABLE public.calendar (
	trade_date date NOT NULL,
	is_trade_day int2 DEFAULT 0 NOT NULL,
	update_time timestamp DEFAULT CURRENT_TIMESTAMP NULL,
	CONSTRAINT calendar_pkey PRIMARY KEY (trade_date),
	CONSTRAINT ck_calendar_is_trade_day CHECK ((is_trade_day = ANY (ARRAY[0, 1])))
);

CREATE TABLE public.factor_pipeline_job (
	id bigserial NOT NULL,
	public_id uuid DEFAULT gen_random_uuid() NOT NULL,
	status varchar(32) NOT NULL,
	source_type varchar(16) NOT NULL,
	run_mode varchar(32) NOT NULL,
	factor_ids text NOT NULL,
	test_universe varchar(32) NULL,
	idempotency_key varchar(128) NULL,
	error_message text NULL,
	result_summary jsonb NULL,
	log_rel_path varchar(512) NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	started_at timestamptz NULL,
	finished_at timestamptz NULL,
	backtest_job_id uuid NULL,
	CONSTRAINT ck_factor_pipeline_job_run_mode CHECK (((run_mode)::text = ANY ((ARRAY['new_only'::character varying, 'full'::character varying, 'revalidate'::character varying, 'quick'::character varying, 'trial'::character varying, 'selection_only'::character varying])::text[]))),
	CONSTRAINT ck_factor_pipeline_job_source_type CHECK (((source_type)::text = ANY ((ARRAY['crawl'::character varying, 'llm'::character varying, 'manual'::character varying])::text[]))),
	CONSTRAINT ck_factor_pipeline_job_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'success'::character varying, 'failed'::character varying])::text[]))),
	CONSTRAINT factor_pipeline_job_pkey PRIMARY KEY (id),
	CONSTRAINT uq_factor_pipeline_job_public_id UNIQUE (public_id),
	CONSTRAINT fk_factor_pipeline_job_backtest_job FOREIGN KEY (backtest_job_id) REFERENCES public.factor_pipeline_job(public_id) ON DELETE SET NULL
);
CREATE INDEX idx_factor_pipeline_job_status_created ON public.factor_pipeline_job USING btree (status, created_at DESC);
CREATE UNIQUE INDEX uq_factor_pipeline_job_idempotency_key_active ON public.factor_pipeline_job USING btree (idempotency_key) WHERE ((idempotency_key IS NOT NULL) AND ((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[])));


CREATE TABLE public.factor_pipeline_job_backtest (
	id bigserial NOT NULL,
	job_public_id uuid NOT NULL,
	factor_backtest_id int8 NOT NULL,
	factor_id varchar(128) NOT NULL,
	created_at timestamptz DEFAULT now() NOT NULL,
	CONSTRAINT factor_pipeline_job_backtest_pkey PRIMARY KEY (id),
	CONSTRAINT uq_pipeline_job_backtest UNIQUE (job_public_id, factor_backtest_id)
);
CREATE INDEX idx_pipeline_job_backtest_factor ON public.factor_pipeline_job_backtest USING btree (factor_id, created_at DESC);
CREATE INDEX idx_pipeline_job_backtest_job ON public.factor_pipeline_job_backtest USING btree (job_public_id, created_at DESC);


-- public.factor_pipeline_job_backtest foreign keys

ALTER TABLE public.factor_pipeline_job_backtest ADD CONSTRAINT fk_pipeline_job_backtest_backtest FOREIGN KEY (factor_backtest_id) REFERENCES public.factor_backtest(id) ON DELETE CASCADE;
ALTER TABLE public.factor_pipeline_job_backtest ADD CONSTRAINT fk_pipeline_job_backtest_job FOREIGN KEY (job_public_id) REFERENCES public.factor_pipeline_job(public_id) ON DELETE CASCADE;
COMMIT;

