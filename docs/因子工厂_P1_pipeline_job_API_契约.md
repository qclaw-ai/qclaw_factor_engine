# P1 阶段 C：`factor_pipeline_job` API 契约

> **分步怎么点 / 怎么 curl**：`docs/因子工厂_P1_pipeline_job_API_使用步骤.md`  
> 实现代码：`src/pipeline_job_api/`，根目录 `run_pipeline_job_api.py` 启动。  
> 表结构：`sql/migrations/005_factor_pipeline_job.sql`（job 主表） + `sql/migrations/006_factor_pipeline_job_backtest_link.sql`（job 与回测结果关联表） + `sql/migrations/007_factor_pipeline_job_add_backtest_job_id.sql`（A1：selection 来源 job 绑定）。  
> OpenAPI：服务启动后访问 `/docs` 或 `/redoc`。

---

## 1. 服务与配置

| 项 | 说明 |
|----|------|
| 数据库 | 使用根 `config.ini` / `config_dev.ini` 的 `[database]`，与 `common.db.DatabaseManager` 一致 |
| 环境 | `PIPELINE_JOB_API_CONFIG` 可设绝对路径 ini（`run_pipeline_job_api.py` 会写入） |
| 全量保护 | `run_mode=full` 时需 `X-Allow-Full-Run: 1` 或环境变量 `ALLOW_FULL=1`（见 P1 阶段 D4） |
| 本地启动 | `python run_pipeline_job_api.py --config config_dev.ini --port 8777`（在仓库 `qclaw_factor_engine` 根目录） |

**部署形态（已定：FastAPI + uvicorn）**：

- 对外由 **本服务** 提供「建任务 / 查任务」；D 阶段 **worker** 仍用同一库、同一表，**不必**经 HTTP 取活（可轮询 DB 或将来再接内部队列）。
- 探针：存活 **`GET /health`**（不连库）；就绪 **`GET /api/v1/ready`**（`SELECT 1` 验库，失败 503）。

---

## 2. 端点

### 2.1 `POST /api/v1/pipeline/jobs` — 创建任务（C1）

**请求体（JSON）**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `factor_ids` | `string[]` | 是 | 至少 1 个，须在 `factor_basic` 已存在，否则 422 |
| `source_type` | `crawl` \| `llm` \| `manual` | 是 | 与 P1 §2 一致 |
| `run_mode` | 同表 `CHECK` 枚举 | 是 | 含 `new_only` / `full` / `revalidate` / `quick` / `trial` / `selection_only` |
| `quick` | `boolean` | 否 | 默认 `false`；为 `true` 时 **强制** `run_mode=quick`（不触发 full 的防护） |
| `test_universe` | `string` \| `null` | 否 | 可空，表示用根配置默认域 |
| `idempotency_key` | `string` \| `null` | 否 | 最长 128；冪等见下文 |
| `backtest_job_id` | `uuid` \| `null` | 条件必填 | 仅 `run_mode=selection_only` 时必填：来源回测任务 `job_id`（`public_id`） |

**`selection_only` 额外约束（A1）**：

- `backtest_job_id` 必填；
- 来源任务必须存在且 `status=success`；
- 来源任务 `run_mode` 仅允许：`new_only` / `revalidate` / `full`；
- 明确禁止来源：`quick` / `trial` / `selection_only`（防止短窗试跑污染准入状态）。

**成功响应** `200`，体为 **任务对象**（见 2.3）。  
冪等合并（同 key 且目标为 `queued`/`running`）仍为 **200**，且 `idempotent_replay: true`。

### 2.2 `GET /api/v1/pipeline/jobs/{job_id}` — 查询（C2）

- `job_id`：创建响应中的 `job_id`（表字段 `public_id`，UUID 字符串即可）。
- `200`：任务对象；`404`：无此 job。

### 2.3 `GET /api/v1/pipeline/jobs/{job_id}/result` — 查询该 job 结果

- `404`：无此 `job_id`。
- `200`：返回 `job_id`、`status`、`ready`、`items[]`（含 `factor_backtest_id`、`ic_value`、`ic_ir`、`backtest_time`、`result_json_rel_path` 等）。
- `ready` 仅表示 `status == success`；`items` 来自 `factor_pipeline_job_backtest` 映射的严格结果集。

### 2.4 任务对象（响应字段）

| 字段 | 说明 |
|------|------|
| `job_id` | 对外主键 = `public_id`（UUID） |
| `status` | `queued` / `running` / `success` / `failed` |
| `source_type` / `run_mode` | 与请求或 worker 更新一致 |
| `factor_ids` | 解析后的字符串数组（存库为逗号分隔文本，兼容） |
| `test_universe` / `backtest_job_id` / `idempotency_key` | 可空（`selection_only` 时 `backtest_job_id` 必填） |
| `error_message` / `result_summary` / `log_rel_path` | 失败原因、JSON 摘要、日志相对路径；首版可均为空，待 D 阶段 worker 回写 |
| `created_at` / `started_at` / `finished_at` | ISO8601 |
| `idempotent_replay` | 仅 `POST` 冪等合并时为 `true` |

---

## 3. 冪等（与阶段 A 定稿一致）

- 有 `idempotency_key` 时：若已存在**进行中**行（`queued`/`running`）→ 返回该行，`200` + `idempotent_replay: true`。
- 若仅存在终态行（`success`/`failed`）→ 允许 **新建一行** 且可复用同一 `idempotency_key`；依赖 005 中的部分唯一索引 `uq_factor_pipeline_job_idempotency_key_active`（全表 `UNIQUE(idempotency_key)` 会与此冲突）。

---

## 4. 错误体（C3）

非 2xx 时，响应 JSON 为：

```json
{
  "error": {
    "code": "UPPER_SNAKE_CASE",
    "message": "人类可读",
    "details": {}
  }
}
```

| HTTP | `code` | 场景 |
|------|--------|------|
| 403 | `FORBIDDEN_FULL_RUN` | 未显式允许而使用 `run_mode=full` |
| 404 | `JOB_NOT_FOUND` | 未知 `job_id` |
| 422 | `FACTOR_NOT_IN_BASIC` | `details.missing_factor_ids` 列出不存在项 |
| 422 | `BACKTEST_JOB_ID_REQUIRED` | `run_mode=selection_only` 但未传 `backtest_job_id` |
| 422 | `BACKTEST_JOB_NOT_FOUND` | `backtest_job_id` 不存在 |
| 422 | `BACKTEST_JOB_NOT_SUCCESS` | `backtest_job_id` 状态不是 `success` |
| 422 | `BACKTEST_JOB_RUN_MODE_FORBIDDEN` | 来源任务是 `quick` / `trial` / `selection_only` |
| 503 | `DATABASE_UNAVAILABLE` / `DATABASE_ERROR` | 库连不上或查询失败 |

Pydantic 参数校验错误仍可能返回 FastAPI 默认 422 结构（`detail` 为校验列表），与上表可并存，前端可只认 `4xx/5xx` 与 `error` 自研体。

### 4.1 使用建议（策略同步）

- **`run_mode=full`**：默认不开放给普通前端入口，仅管理员/运维受控入口可触发；并保留 `X-Allow-Full-Run: 1` / `ALLOW_FULL=1` 防护。
- **即使前端不做 quick**，也建议保留 `stage` 隔离（见 `docs/因子工厂_P1_增量编排与前端任务隔离_修正步骤.md`），避免 `new_only`/手工任务等写入污染增量编排的 `MAX(date_end)` 语义。
- 前端默认提交流程建议使用 `new_only`（首跑入 `candidate`），不要直接走 `production`。

---

## 5. 参考

- 主流程：`docs/因子工厂_P1_新增因子入库与回测_详细步骤.md`（阶段 C / A 定稿 / §6.1）
- 库表约定：`docs/complete/db_conventions.md`
