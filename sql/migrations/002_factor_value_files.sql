-- 因子侧真分域：按 (factor_id, universe, artifact_type, 区间/交易日) 管理因子值路径。
-- 说明：
-- 1) 本迁移仅新增路径索引表，不改变现有 factor_files 的兼容语义；
-- 2) rel_path 统一存相对仓库根的 POSIX 路径；
-- 3) artifact_type 约定：
--    - batch_csv: 批量/大回测使用的区间因子值（date_start/date_end 必填，trade_date 为空）
--    - daily_csv: 日更单日因子值（trade_date 必填，date_start/date_end 为空）

BEGIN;

CREATE TABLE IF NOT EXISTS factor_value_files (
    id            serial PRIMARY KEY,
    factor_id     varchar(128) NOT NULL,
    universe      varchar(64)  NOT NULL,
    artifact_type varchar(32)  NOT NULL,
    rel_path      varchar(1024) NOT NULL,
    date_start    date,
    date_end      date,
    trade_date    date,
    created_at    timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    comment       text,
    CONSTRAINT ck_factor_value_files_artifact_type
        CHECK (artifact_type IN ('batch_csv', 'daily_csv')),
    CONSTRAINT ck_factor_value_files_batch_fields
        CHECK (
            artifact_type <> 'batch_csv'
            OR (
                date_start IS NOT NULL
                AND date_end IS NOT NULL
                AND trade_date IS NULL
            )
        ),
    CONSTRAINT ck_factor_value_files_daily_fields
        CHECK (
            artifact_type <> 'daily_csv'
            OR (
                trade_date IS NOT NULL
                AND date_start IS NULL
                AND date_end IS NULL
            )
        )
);

ALTER TABLE factor_value_files
    ADD CONSTRAINT fk_factor_value_files_factor
    FOREIGN KEY (factor_id) REFERENCES factor_basic (factor_id)
    ON UPDATE CASCADE
    ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_value_files_batch
    ON factor_value_files (factor_id, universe, artifact_type, date_start, date_end)
    WHERE artifact_type = 'batch_csv';

CREATE UNIQUE INDEX IF NOT EXISTS uq_factor_value_files_daily
    ON factor_value_files (factor_id, universe, artifact_type, trade_date)
    WHERE artifact_type = 'daily_csv';

CREATE INDEX IF NOT EXISTS idx_factor_value_files_factor_universe_type
    ON factor_value_files (factor_id, universe, artifact_type, created_at DESC);

COMMENT ON TABLE factor_value_files IS
    '真分域因子值路径表：按 factor_id + universe + artifact_type 管理 CSV 等产物路径。';

COMMENT ON COLUMN factor_value_files.rel_path IS
    '因子值文件相对仓库根的 POSIX 路径。';

COMMIT;

