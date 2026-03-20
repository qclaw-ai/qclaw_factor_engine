-- PostgreSQL：日更因子值路径（与评估线 factor_values_path 分离）
-- 若已手工加列，本脚本可重复执行（IF NOT EXISTS）

ALTER TABLE factor_files
    ADD COLUMN IF NOT EXISTS factor_values_path_daily varchar(1024);

COMMENT ON COLUMN factor_files.factor_values_path_daily IS
    '日更/执行用因子值 CSV 路径（相对仓库根 POSIX）；策略侧优先读此列，空则回退 factor_values_path';
