-- 在 factor_value_files 增加批次治理字段，支持增量 / rebase 优先读取。

BEGIN;

ALTER TABLE factor_value_files
    ADD COLUMN IF NOT EXISTS batch_id varchar(128),
    ADD COLUMN IF NOT EXISTS stage varchar(16) NOT NULL DEFAULT 'candidate',
    ADD COLUMN IF NOT EXISTS is_rebase boolean NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ck_factor_value_files_stage'
    ) THEN
        ALTER TABLE factor_value_files
            ADD CONSTRAINT ck_factor_value_files_stage
            CHECK (stage IN ('candidate', 'production', 'deprecated'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_factor_value_files_batch_stage_cov
    ON factor_value_files (
        factor_id,
        universe,
        artifact_type,
        stage,
        is_rebase,
        date_start,
        date_end,
        created_at DESC
    )
    WHERE artifact_type = 'batch_csv';

COMMIT;

