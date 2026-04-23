-- P1 A1：selection_only 来源任务绑定字段
-- 为 factor_pipeline_job 增加 backtest_job_id（引用来源 backtest job public_id）

BEGIN;

ALTER TABLE factor_pipeline_job
    ADD COLUMN IF NOT EXISTS backtest_job_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_factor_pipeline_job_backtest_job'
    ) THEN
        ALTER TABLE factor_pipeline_job
            ADD CONSTRAINT fk_factor_pipeline_job_backtest_job
            FOREIGN KEY (backtest_job_id)
            REFERENCES factor_pipeline_job (public_id)
            ON DELETE SET NULL;
    END IF;
END $$;

COMMIT;
