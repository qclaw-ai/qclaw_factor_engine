# 配置键调用矩阵（根配置）

> 适用版本：`qclaw_factor_engine` 配置硬切换后（默认只读仓库根 `config.ini/config_dev.ini`）。
>
> 优先级：`CLI --config` > 根配置文件键值 > 代码 fallback。

---

## [database]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `database.db_host` | `src/common/db.py` -> `get_db_manager()` | 数据库连接主机 |
| `database.db_port` | `src/common/db.py` -> `get_db_manager()` | 数据库连接端口 |
| `database.db_user` | `src/common/db.py` -> `get_db_manager()` | 数据库用户名 |
| `database.db_password` | `src/common/db.py` -> `get_db_manager()` | 数据库密码 |
| `database.db_name` | `src/common/db.py` -> `get_db_manager()` | 数据库名 |

---

## [paths]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `paths.factor_docs_dir` | `src/factor_docs/factor_docs_parser.py` -> `load_all_factors()` | 因子文档根目录 |
| `paths.factor_docs_dir` | `src/factor_crawler/factor_crawler_runner.py` -> `run_factor_crawler()` | 抓取因子文档输出目录 |
| `paths.factor_docs_dir` | `src/factor_md_generation/io_paths.py` -> `resolve_factor_docs_dir()` | MD 生成模块文档落盘根目录 |
| `paths.backtest_results_dir` | `src/backtest_io/backtest_io_runner.py` -> `run_backtest_io()` | 回测 JSON 输出目录 |
| `paths.backtest_results_dir` | `src/selection_and_store/selection_and_store_runner.py` -> `run_selection_and_store()` | 回测 JSON 相对路径拼装 |
| `paths.output_dir` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 月监报告输出目录 |
| `paths.sync_factor_values_execution_universe` | `src/backtest_io/sync_factor_values_path_runner.py` -> `main()` | 同步旧字段时使用的域 |
| `paths.logs_dir` | 当前无直接读取（保留） | 统一日志目录预留键 |

---

## [jq]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `jq.user` | `src/factor_engine/factor_engine_runner.py` -> `_auth_jq_if_configured()` | 分域股票池解析时聚宽登录 |
| `jq.password` | `src/factor_engine/factor_engine_runner.py` -> `_auth_jq_if_configured()` | 分域股票池解析时聚宽登录 |
| `jq.user` | `src/data_ingest/data_ingest_stock_daily_jq_initial.py` -> `main()` | JQ 历史导入登录 |
| `jq.password` | `src/data_ingest/data_ingest_stock_daily_jq_initial.py` -> `main()` | JQ 历史导入登录 |
| `jq.user` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_auth_jq()` / `_auth_jq_if_needed()` | MySQL 不可用时回退聚宽 |
| `jq.password` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_auth_jq()` / `_auth_jq_if_needed()` | MySQL 不可用时回退聚宽 |

---

## [daily]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `daily.scope` | `src/daily_factor_values/daily_factor_values_runner.py` -> `main()` | `valid_only` / `all_in_basic` |
| `daily.universe` | `src/daily_factor_values/daily_factor_values_runner.py` -> `main()` | 日更所属域 |

---

## [factor_engine]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `factor_engine.start_date` | `src/factor_engine/factor_engine_runner.py` -> `run_factor_engine()` | 因子计算起始日期 |
| `factor_engine.end_date` | `src/factor_engine/factor_engine_runner.py` -> `run_factor_engine()` | 因子计算结束日期 |
| `factor_engine.factor_ids` | `src/factor_engine/factor_engine_runner.py` -> `run_factor_engine()` | 指定因子集合；若调用时传入 `factor_ids_override` 或命令行 `--factor-ids` 则**以参数为准，忽略本项** |
| `factor_engine.universe` | `src/factor_engine/factor_engine_runner.py` -> `run_factor_engine()` | 因子计算域；若 `universe_override` 或 `--universe` 则**以参数为准，忽略本项** |
| `factor_engine.batch_id` | `src/factor_engine/factor_engine_runner.py` -> `run_factor_engine()` | 批次号 |
| `factor_engine.skip_factor_ids` | `src/factor_engine/factor_engine_runner.py` -> `run_factor_engine()` | 跳过因子集合 |
| `factor_engine.config_file` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 月监中行情加载复用的配置文件路径（默认当前 config） |

---

## [factor_incremental]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `factor_incremental.factor_engine_config_file` | `src/factor_incremental/factor_incremental_runner.py` -> `run_factor_incremental()` | 增量编排调用的 factor_engine 配置路径 |
| `factor_incremental.mode` | `src/factor_incremental/factor_incremental_runner.py` -> `run_factor_incremental()` | `incremental` / `rebase` |
| `factor_incremental.stage` | `src/factor_incremental/factor_incremental_runner.py` -> `run_factor_incremental()` | 写入阶段 |
| `factor_incremental.warmup_trading_days` | `src/factor_incremental/factor_incremental_runner.py` -> `run_factor_incremental()`；`src/daily_factor_values/daily_factor_values_runner.py` -> `run_daily_factor_values()` | 引擎计算区间起点前移（交易日 warmup；日更与增量共用） |

---

## [backtest]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `backtest.horizon` | `src/backtest_core/backtest_core_runner.py` -> `run_backtest()` | 前向收益 horizon |
| `backtest.n_quantiles` | `src/backtest_core/backtest_core_runner.py` -> `run_backtest()` | 分组数 |
| `backtest.factor_output_dir` | `src/backtest_core/backtest_core_runner.py` -> `run_backtest()` | 因子 CSV 根目录 |
| `backtest.use_factor_value_files` | `src/backtest_core/backtest_core_runner.py` -> `run_backtest()` | 是否按 `factor_value_files` 选路径 |
| `backtest.factor_ids` | `src/backtest_core/backtest_core_runner.py` -> `run_backtest()` | 回测因子过滤；若 `factor_ids_override` 或 CLI `--factor-ids` 则**以参数为准，忽略本项** |
| `backtest.test_universe` | `src/backtest_core/backtest_core_runner.py` -> `run_backtest()` | 回测实证域；若 `test_universe_override` 或 `--test-universe` 则**以参数为准，忽略本项**（参数会经 `normalize_universe_code`） |

---

## [selection]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `selection.scene` | `src/selection_and_store/selection_and_store_runner.py` -> `run_selection_and_store()` | 阈值场景 |
| `selection.primary_universe_for_file_pointer` | `src/selection_and_store/selection_and_store_runner.py` -> `run_selection_and_store()` | `factor_files.backtest_json_path` 主域指针 |

---

## [factor_corr]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `factor_corr.enable` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 是否执行相关性任务 |
| `factor_corr.test_universe` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 相关性实证域 |
| `factor_corr.window_days` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 计算窗口天数 |
| `factor_corr.min_overlap_days` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 最小重叠天数 |
| `factor_corr.redis_key_prefix` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | Redis key 前缀 |
| `factor_corr.keep_days` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 历史 key 保留天数 |
| `factor_corr.factor_output_root` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 因子值目录根 |
| `factor_corr.factor_ids` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 指定因子过滤 |
| `factor_corr.as_of_date` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 计算锚点日 |
| `factor_corr.use_factor_value_files` | `src/factor_corr/factor_corr_matrix.py` -> `run_factor_corr_matrix()` | 是否从 `factor_value_files` 选路径 |

---

## [redis]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `redis.host` | `src/factor_corr/factor_corr_matrix.py` -> `_connect_redis()` | Redis 主机 |
| `redis.port` | `src/factor_corr/factor_corr_matrix.py` -> `_connect_redis()` | Redis 端口 |
| `redis.db` | `src/factor_corr/factor_corr_matrix.py` -> `_connect_redis()` | Redis 库 |
| `redis.password` | `src/factor_corr/factor_corr_matrix.py` -> `_connect_redis()` | Redis 密码 |

---

## [monitor]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `monitor.universe` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 月监域 |
| `monitor.horizon_days` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 前向收益 horizon |
| `monitor.monitor_window_trading_days` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 月监窗口长度 |
| `monitor.warmup_trading_days` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 预热长度 |
| `monitor.threshold_scene` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 阈值场景 |
| `monitor.max_factors` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 候选因子数量上限 |
| `monitor.parity_sample_factors` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 对账抽样因子数 |
| `monitor.parity_sample_days` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 对账抽样日期数 |
| `monitor.parity_tolerance` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 对账容差 |
| `monitor.random_seed` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 抽样随机种子 |
| `monitor.coverage_min_fallback` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 阈值回退：覆盖率 |
| `monitor.ic_mean_min_fallback` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 阈值回退：IC 均值 |
| `monitor.ic_ir_min_fallback` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 阈值回退：IC_IR |

---

## [factor_docs]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `factor_docs.config_file` | `src/factor_monitor/factor_monitor_runner.py` -> `run_factor_monitor()` | 月监读取因子定义时使用的配置路径（默认当前 config） |

---

## [data_ingest]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `data_ingest.mode` | `src/data_ingest/data_ingest_stock_daily.py` -> `main()` | `full` / `daily` |
| `data_ingest.universe` | `src/data_ingest/data_ingest_stock_daily.py` -> `main()` / `_resolve_stock_codes()` | 导入域 |
| `data_ingest.stock_codes` | `src/data_ingest/data_ingest_stock_daily.py` -> `_resolve_stock_codes()` | 自定义股票列表 |
| `data_ingest.start_date` | `src/data_ingest/data_ingest_stock_daily.py` -> `main()` | 全量开始日期 |
| `data_ingest.end_date` | `src/data_ingest/data_ingest_stock_daily.py` -> `main()` | 全量结束日期 |
| `data_ingest.adjust` | `src/data_ingest/data_ingest_stock_daily.py` -> `main()` | 复权方式 |
| `data_ingest.daily_lookback_days` | `src/data_ingest/data_ingest_stock_daily.py` -> `main()` | 日更模式回看天数 |

---

## [data_ingest_jq_initial]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `data_ingest_jq_initial.start_date` | `src/data_ingest/data_ingest_stock_daily_jq_initial.py` -> `main()` | 历史导入开始日期 |
| `data_ingest_jq_initial.end_date` | `src/data_ingest/data_ingest_stock_daily_jq_initial.py` -> `main()` | 历史导入结束日期 |
| `data_ingest_jq_initial.universe` | `src/data_ingest/data_ingest_stock_daily_jq_initial.py` -> `main()` / `_resolve_stock_codes()` | 导入域 |
| `data_ingest_jq_initial.stock_codes` | `src/data_ingest/data_ingest_stock_daily_jq_initial.py` -> `_resolve_stock_codes()` | 自定义股票列表 |
| `data_ingest_jq_initial.batch_size` | `src/data_ingest/data_ingest_stock_daily_jq_initial.py` -> `main()` | 批量拉取大小 |

---

## [mysql_source]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `mysql_source.enabled` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_build_mysql_source_engine()` | 是否启用 MySQL 源 |
| `mysql_source.host` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_build_mysql_source_engine()` | MySQL 主机 |
| `mysql_source.port` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_build_mysql_source_engine()` | MySQL 端口 |
| `mysql_source.user` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_build_mysql_source_engine()` | MySQL 用户 |
| `mysql_source.password` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_build_mysql_source_engine()` | MySQL 密码 |
| `mysql_source.db_name` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_build_mysql_source_engine()` | MySQL 数据库 |
| `mysql_source.dailyquote_table` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_sync_stock_daily_from_mysql()` | 行情源表名 |
| `mysql_source.calendar_table` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_sync_calendar_from_mysql()` | 日历源表名 |
| `mysql_source.dailyquote_tradedate_format` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_sync_stock_daily_from_mysql()` | 行情日期格式 |
| `mysql_source.calendar_tradedate_format` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_sync_calendar_from_mysql()` | 日历日期格式 |
| `mysql_source.calendar_sync_mode` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_sync_calendar_from_mysql()` | 日历同步模式 |
| `mysql_source.stock_sync_chunk_days` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_sync_stock_daily_from_mysql()` | 行情分块同步天数 |
| `mysql_source.symbol_filter` | `src/data_ingest/daily_stock_and_calendar_sync.py` -> `_sync_stock_daily_from_mysql()` | 是否按股票池过滤 |

---

## [crawler]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `crawler.sources` | `src/factor_crawler/factor_crawler_runner.py` -> `run_factor_crawler()` | 启用数据源列表 |
| `crawler.max_factors_per_run` | `src/factor_crawler/factor_crawler_runner.py` -> `run_factor_crawler()` | 单次最大生成因子数 |

---

## [sources.fd]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `sources.fd.base_url` | `src/factor_crawler/factor_crawler_runner.py` -> `run_factor_crawler()` | factors.directory 入口地址 |
| `sources.fd.search_keywords` | `src/factor_crawler/factor_crawler_runner.py` -> `run_factor_crawler()` | 抓取关键词 |

---

## [llm_gateway]

| section.key | 调用模块 / 函数 | 用途 |
|---|---|---|
| `llm_gateway.baseUrl` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | LLM 网关地址 |
| `llm_gateway.apiKey` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | LLM 网关 key（次于环境变量） |
| `llm_gateway.model` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | 文本模型 |
| `llm_gateway.thinking_type` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | thinking 配置 |
| `llm_gateway.timeout_sec` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | 超时秒数 |
| `llm_gateway.max_tokens` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | 输出 token 上限 |
| `llm_gateway.temperature` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | 采样温度 |
| `llm_gateway.vision_model` | `src/factor_md_generation/llm_md/llm_client.py` -> `_load_llm_gateway_config()` | 图像识别模型 |

---

## 备注

- 本文档基于当前代码中的 `cfg.get/getint/getfloat/getboolean` 调用点整理。
- 若新增配置键，请同步更新：
  - `config_sample.ini`
  - 本文件 `docs/config_keys_matrix.md`
