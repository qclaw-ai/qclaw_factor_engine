-- 多领域因子大回测：factor_backtest 增加实证域；新增 factor_universe_status。
-- 在目标库执行前请备份。已有行 test_universe 回填为 LEGACY，便于与迁移后新数据区分。

BEGIN;

-- 1) factor_backtest：实证股票池 + 可选回测 JSON 相对路径
ALTER TABLE factor_backtest
    ADD COLUMN IF NOT EXISTS test_universe varchar(64);

UPDATE factor_backtest
SET test_universe = 'LEGACY'
WHERE test_universe IS NULL;

ALTER TABLE factor_backtest
    ALTER COLUMN test_universe SET NOT NULL;

ALTER TABLE factor_backtest
    ADD COLUMN IF NOT EXISTS result_json_rel_path varchar(1024);

COMMENT ON COLUMN factor_backtest.test_universe IS '大回测实证域（如 ALL_A / HS300 / ZZ500），与因子值全市场一份并存';
COMMENT ON COLUMN factor_backtest.result_json_rel_path IS '该次领域回测结果 JSON 相对仓库根路径';

CREATE INDEX IF NOT EXISTS idx_factor_backtest_factor_universe_time
    ON factor_backtest (factor_id, test_universe, backtest_time DESC);

-- 2) 按 (因子, 领域) 维护是否过阈（权威）；factor_basic.is_valid 为派生兼容字段
CREATE TABLE IF NOT EXISTS factor_universe_status (
    factor_id     varchar(128) NOT NULL,
    test_universe varchar(64)  NOT NULL,
    is_valid      boolean      NOT NULL DEFAULT FALSE,
    updated_at    timestamp    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (factor_id, test_universe)
);

ALTER TABLE factor_universe_status
    ADD CONSTRAINT fk_factor_universe_status_factor
    FOREIGN KEY (factor_id) REFERENCES factor_basic (factor_id)
    ON UPDATE CASCADE
    ON DELETE CASCADE;

COMMENT ON TABLE factor_universe_status IS '因子在各实证域上的有效位；factor_basic.is_valid = 是否存在任一为 TRUE';

COMMIT;
