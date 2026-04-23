-- P1：job 结果关联表（严格按 job 查询本次产出的 factor_backtest）
-- 说明：
-- 1) 一条 job 可能产出多条 factor_backtest（多因子）；
-- 2) 该表只做映射，不替代 factor_backtest 业务字段；
-- 3) 删除 job 或 backtest 行时级联删除映射。

BEGIN;

CREATE TABLE IF NOT EXISTS factor_pipeline_job_backtest (
    id                 bigserial PRIMARY KEY,
    job_public_id      uuid NOT NULL,
    factor_backtest_id bigint NOT NULL,
    factor_id          varchar(128) NOT NULL,
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_pipeline_job_backtest UNIQUE (job_public_id, factor_backtest_id),
    CONSTRAINT fk_pipeline_job_backtest_job
        FOREIGN KEY (job_public_id)
        REFERENCES factor_pipeline_job (public_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_pipeline_job_backtest_backtest
        FOREIGN KEY (factor_backtest_id)
        REFERENCES factor_backtest (id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pipeline_job_backtest_job
    ON factor_pipeline_job_backtest (job_public_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_job_backtest_factor
    ON factor_pipeline_job_backtest (factor_id, created_at DESC);

COMMIT;
