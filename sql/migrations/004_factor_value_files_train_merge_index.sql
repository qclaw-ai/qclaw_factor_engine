-- P2 第4步：为训练侧“多文件拼接”查询补充索引（PostgreSQL）
-- 目的：加速按 factor_id/universe/artifact_type + 区间相交查询，并服务优先级排序。

BEGIN;

CREATE INDEX IF NOT EXISTS idx_fvf_train_merge_lookup
    ON factor_value_files (
        factor_id,
        universe,
        artifact_type,
        is_rebase,
        created_at DESC,
        id DESC,
        date_start,
        date_end
    )
    WHERE artifact_type = 'batch_csv';

COMMIT;

