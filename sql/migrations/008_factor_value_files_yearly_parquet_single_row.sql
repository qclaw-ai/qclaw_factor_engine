-- factor_value_files: 支持 yearly_parquet（单因子-按年单行覆盖）
-- 目标：
-- 1) 保留历史 batch_csv / daily_csv 兼容能力；
-- 2) 新增 yearly_parquet 所需字段 year/updated_at/last_batch_id；
-- 3) 通过唯一键 (factor_id, universe, artifact_type, year) 保证 yearly 单行覆盖。

BEGIN;

ALTER TABLE factor_value_files
    ADD COLUMN IF NOT EXISTS year integer,
    ADD COLUMN IF NOT EXISTS updated_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ADD COLUMN IF NOT EXISTS last_batch_id varchar(128);

-- 统一 artifact_type 枚举，加入 yearly_parquet。
ALTER TABLE factor_value_files
    DROP CONSTRAINT IF EXISTS ck_factor_value_files_artifact_type;

ALTER TABLE factor_value_files
    ADD CONSTRAINT ck_factor_value_files_artifact_type
    CHECK (artifact_type IN ('batch_csv', 'daily_csv', 'yearly_parquet'));

-- batch 字段约束：batch_csv 必须 date_start/date_end，且 trade_date/year 为空。
ALTER TABLE factor_value_files
    DROP CONSTRAINT IF EXISTS ck_factor_value_files_batch_fields;

ALTER TABLE factor_value_files
    ADD CONSTRAINT ck_factor_value_files_batch_fields
    CHECK (
        artifact_type <> 'batch_csv'
        OR (
            date_start IS NOT NULL
            AND date_end IS NOT NULL
            AND trade_date IS NULL
            AND year IS NULL
        )
    );

-- daily 字段约束：daily_csv 必须 trade_date，且 date_start/date_end/year 为空。
ALTER TABLE factor_value_files
    DROP CONSTRAINT IF EXISTS ck_factor_value_files_daily_fields;

ALTER TABLE factor_value_files
    ADD CONSTRAINT ck_factor_value_files_daily_fields
    CHECK (
        artifact_type <> 'daily_csv'
        OR (
            trade_date IS NOT NULL
            AND date_start IS NULL
            AND date_end IS NULL
            AND year IS NULL
        )
    );

-- yearly 字段约束：yearly_parquet 必须 year + date_start/date_end，且 trade_date 为空。
ALTER TABLE factor_value_files
    DROP CONSTRAINT IF EXISTS ck_factor_value_files_yearly_fields;

ALTER TABLE factor_value_files
    ADD CONSTRAINT ck_factor_value_files_yearly_fields
    CHECK (
        artifact_type <> 'yearly_parquet'
        OR (
            year IS NOT NULL
            AND date_start IS NOT NULL
            AND date_end IS NOT NULL
            AND trade_date IS NULL
            AND date_start <= date_end
            AND EXTRACT(YEAR FROM date_start) = year
            AND EXTRACT(YEAR FROM date_end) = year
        )
    );

-- yearly_parquet 单行覆盖唯一键（同因子/域/年只能一行）。
CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_value_files_yearly
    ON factor_value_files (factor_id, universe, artifact_type, year)
    WHERE artifact_type = 'yearly_parquet';

-- yearly 查询常用索引：按因子/域/阶段查当前年文件。
CREATE INDEX IF NOT EXISTS idx_factor_value_files_yearly_lookup
    ON factor_value_files (
        factor_id,
        universe,
        artifact_type,
        stage,
        year,
        updated_at DESC
    )
    WHERE artifact_type = 'yearly_parquet';

COMMIT;

