-- P1：因子管线任务表（试跑 / 准入编排的 job 元数据，与业务表 factor_backtest 等解耦）
-- 说明见：docs/因子工厂_P1_新增因子入库与回测_详细步骤.md
-- 冪等策略见该文档「阶段 A 定稿」：idempotency_key 仅在 (queued, running) 上唯一，允许多行终态同 key
-- 若曾用旧版全表 UNIQUE(idempotency_key) 建表，需先 DROP 后按本脚本重建

BEGIN;

CREATE TABLE IF NOT EXISTS factor_pipeline_job (
    id              bigserial PRIMARY KEY,
    public_id       uuid NOT NULL DEFAULT gen_random_uuid(),
    status          varchar(32) NOT NULL,
    source_type     varchar(16) NOT NULL,
    run_mode        varchar(32) NOT NULL,
    factor_ids      text NOT NULL,
    test_universe   varchar(32),
    backtest_job_id uuid,
    idempotency_key varchar(128),
    error_message   text,
    result_summary  jsonb,
    log_rel_path    varchar(512),
    created_at      timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    finished_at     timestamptz,
    CONSTRAINT uq_factor_pipeline_job_public_id UNIQUE (public_id),
    CONSTRAINT fk_factor_pipeline_job_backtest_job
        FOREIGN KEY (backtest_job_id)
        REFERENCES factor_pipeline_job (public_id)
        ON DELETE SET NULL,
    CONSTRAINT ck_factor_pipeline_job_status
        CHECK (status IN ('queued', 'running', 'success', 'failed')),
    CONSTRAINT ck_factor_pipeline_job_source_type
        CHECK (source_type IN ('crawl', 'llm', 'manual')),
    CONSTRAINT ck_factor_pipeline_job_run_mode
        CHECK (run_mode IN ('new_only', 'full', 'revalidate', 'quick', 'trial', 'selection_only'))
);

COMMENT ON TABLE factor_pipeline_job IS 'P1 管线任务：过程态；业务结果见 factor_backtest / factor_value_files 等';

CREATE INDEX IF NOT EXISTS idx_factor_pipeline_job_status_created
    ON factor_pipeline_job (status, created_at DESC);

-- 同 idempotency_key 在终态可多条；仅进行中的行唯一（与阶段 A 冪等合并一致）
CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_pipeline_job_idempotency_key_active
    ON factor_pipeline_job (idempotency_key)
    WHERE idempotency_key IS NOT NULL
      AND status IN ('queued', 'running');

COMMIT;
