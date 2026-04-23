# P1 阶段 C：`pipeline_job` API 详细使用步骤

> 契约与错误码见：`docs/因子工厂_P1_pipeline_job_API_契约.md`。  
> 主流程与阶段划分见：`docs/因子工厂_P1_新增因子入库与回测_详细步骤.md`。

---

## 一、前置条件

1. **PostgreSQL 已建表**  
   执行过：  
   - `sql/migrations/005_factor_pipeline_job.sql`（job 主表 + 冪等部分唯一索引）  
   - `sql/migrations/006_factor_pipeline_job_backtest_link.sql`（job 与 `factor_backtest` 关联表，用于严格按 job 查结果）  
   - `sql/migrations/007_factor_pipeline_job_add_backtest_job_id.sql`（A1：selection_only 来源回测 job 绑定）  
   若旧表用全表 `UNIQUE(idempotency_key)` 建过，需先 `DROP` 后按 005 重建。

2. **根配置可连库**  
   根目录 `config.ini` 或 `config_dev.ini` 中 `[database]`（`db_host` / `db_port` / `db_user` / `db_password` / `db_name`）与现网/本机库一致。  
   非 `ENV=prod` 时，代码里 `Config` 会优先使用 `*_dev.ini`（与现有 runner 行为一致，见 `common.config.Config`）。

3. **Python 依赖**  
   在仓库 `qclaw_factor_engine` 根目录已安装 `requirements.txt`（含 `fastapi`、`uvicorn[standard]`、`SQLAlchemy`、`psycopg2-binary` 等）。

4. **业务表**  
   创建任务时 API 会校验 `factor_ids` 是否在 `factor_basic` 中存在。若库里还没有测试因子，请先插入至少一行 `factor_basic`，或改用真实存在的 `factor_id`。

---

## 二、启动服务

1. 打开终端，**进入仓库根目录**（与 `run_pipeline_job_api.py` 同级）：

   ```text
   cd D:\programer\tdenergy\repo\qclaw_factor_engine
   ```

2. 启动（**开发**常用 `config_dev.ini`；按你本机改路径与端口）：

   ```text
   python run_pipeline_job_api.py --config config_dev.ini --port 8777
   ```

3. 成功时终端会出现 uvicorn 监听日志；脚本会把绝对路径写入环境变量 `PIPELINE_JOB_API_CONFIG`，应用内用该文件读 `[database]`。

4. **业务日志**（`create_job_queued` / `job_store` 等，INFO）：与 uvicorn 的 `GET/POST` **同一窗口**输出，形如 `... [INFO] pipeline_job_api.job_store: create_job_queued: ...`。由 `app.create_app()` 为 `pipeline_job_api` 挂了控制台 Handler；若你只看到 `127.0.0.1 - "POST` 行而没有 `job_store` 行，先确认已更新到带 `_configure_pipeline_job_api_stream_logging` 的 `app.py` 并重启进程。

5. 可选：指定监听地址（默认 `0.0.0.0`）：

   ```text
   python run_pipeline_job_api.py --config config_dev.ini --host 127.0.0.1 --port 8777
   ```

6. 停止服务：在运行窗口按 `Ctrl+C`。

---

## 三、健康检查

| 地址 | 含义 |
|------|------|
| `http://127.0.0.1:8777/health` | 存活：不连库，应返回 `{"status":"ok",...}` |
| `http://127.0.0.1:8777/api/v1/ready` | 就绪：对库 `SELECT 1`；连不上为 **503** |
| `http://127.0.0.1:8777/docs` | **Swagger**（推荐日常调试） |
| `http://127.0.0.1:8777/redoc` | 只读 ReDoc 文档 |

若 `/api/v1/ready` 为 503，先检查 `config`、防火墙、本机 PG 是否允许连接。

---

## 四、用 Swagger 体验创建与查询

1. 浏览器打开：`http://127.0.0.1:8777/docs`

2. 找到 **`POST /api/v1/pipeline/jobs`**，点 **「Try it out」**。

3. **Request body** 填 JSON，示例（把 `你的_FACTOR_ID` 换成库中真实 `factor_id`）：

   ```json
   {
     "factor_ids": ["你的_FACTOR_ID"],
     "source_type": "manual",
     "run_mode": "new_only"
   }
   ```

4. 点 **「Execute」**。成功则 **HTTP 200**，响应体含 **`job_id`**（UUID 格式）、`status`（新任务一般为 `queued`）、`created_at` 等。

5. 再找到 **`GET /api/v1/pipeline/jobs/{job_id}`**，把上一步的 **`job_id` 原样** 填到路径参数，执行，应 **200** 且与创建时信息一致。

6. **无 worker 时**：`status` 会**一直**是 `queued` 直到 P1 阶段 D 的 worker 消费并更新，属**预期**。

7. worker 成功后，调用 **`GET /api/v1/pipeline/jobs/{job_id}/result`**，应返回该 job 关联的 `items`（每项含 `factor_backtest_id`、`ic_value`、`ic_ir`、`result_json_rel_path` 等）。

### 4.1 常用可选字段

- **`idempotency_key`**（字符串，最长 128）  
  同一 key 在已有 **`queued` / `running`** 任务时再次 `POST` → 返回**同一** `job_id`，**不新增行**，体里可有 `idempotent_replay: true`。  
  终态为 `success` / `failed` 后，**可以**用同一 key 再建**新**一条任务（依赖 005 的**部分唯一**索引，不要用手滑的全表 `UNIQUE(idempotency_key)` 旧建表方式）。

- **`test_universe`**  
  可空；空表示不写入本字段（D 里 worker 可与根配置 `[backtest].test_universe` 等对齐）。P1 建议同一条 job 内与 `factor_engine` 的域**保持一致**。

- **`quick`: true**  
  会**强制** `run_mode=quick`（由 worker 与根配置消费），且不触发下述 `full` 防护。

- **`run_mode=full`**  
  需请求头 **`X-Allow-Full-Run: 1`**，或进程环境变量 **`ALLOW_FULL=1`**，否则 **403** `FORBIDDEN_FULL_RUN`（防误跑全量）。

- **`run_mode=selection_only`（A1 规则）**  
  必须传 `backtest_job_id`，且该来源任务必须是 `success` 且 `run_mode` 属于 `new_only/revalidate/full`。  
  来源为 `quick/trial/selection_only` 会被 API 以 422 拒绝（防止短窗测试污染 `factor_universe_status`）。

---

## 五、用 curl 复现（可选）

在**另一终端**（服务保持运行）：

**创建：**

```text
curl -s -H "Content-Type: application/json" -d "{\"factor_ids\":[\"你的_FACTOR_ID\"],\"source_type\":\"manual\",\"run_mode\":\"new_only\"}" http://127.0.0.1:8777/api/v1/pipeline/jobs
```

**查询（把 UUID 换成响应里的 `job_id`）：**

```text
curl -s http://127.0.0.1:8777/api/v1/pipeline/jobs/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

**带冪等键的创建：**

```text
curl -s -H "Content-Type: application/json" -d "{\"factor_ids\":[\"你的_FACTOR_ID\"],\"source_type\":\"manual\",\"run_mode\":\"new_only\",\"idempotency_key\":\"demo-key-1\"}" http://127.0.0.1:8777/api/v1/pipeline/jobs
```

对同一 `idempotency_key` 连发两次，应得到相同 `job_id`（在仍为 `queued`/`running` 时）。

**显式全量试跑（慎用）：**

```text
curl -s -H "Content-Type: application/json" -H "X-Allow-Full-Run: 1" -d "{\"factor_ids\":[\"你的_FACTOR_ID\"],\"source_type\":\"manual\",\"run_mode\":\"full\"}" http://127.0.0.1:8777/api/v1/pipeline/jobs
```

**创建 selection_only（引用已成功的来源回测任务）：**

```text
curl -s -H "Content-Type: application/json" -d "{\"factor_ids\":[\"你的_FACTOR_ID\"],\"source_type\":\"manual\",\"run_mode\":\"selection_only\",\"backtest_job_id\":\"xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx\"}" http://127.0.0.1:8777/api/v1/pipeline/jobs
```

---

## 六、在数据库中核对

用 `psql` 或任意客户端查表 `factor_pipeline_job`：

- 新任务有 **`public_id` = 响应里 `job_id`**
- **`status`** 与接口一致
- **`factor_ids`** 为逗号分隔存库（接口响应里为数组是解析后的展示）

```sql
SELECT public_id, status, run_mode, factor_ids, created_at
FROM factor_pipeline_job
ORDER BY id DESC
LIMIT 5;
```

---

## 七、常见错误与处理

| 现象 | 处理 |
|------|------|
| 422，body 含 `FACTOR_NOT_IN_BASIC` | `factor_id` 不在 `factor_basic`；在库里先建因子或改请求 |
| 503，Database unavailable | 检查 `[database]`、密码、本机/远程 PG 是否可连；试 `/api/v1/ready` |
| 403，full 被拦 | 非误触时加 `X-Allow-Full-Run: 1` 或 `ALLOW_FULL=1` |
| 422，`BACKTEST_JOB_ID_REQUIRED` | `selection_only` 未传 `backtest_job_id` |
| 422，`BACKTEST_JOB_NOT_FOUND` / `BACKTEST_JOB_NOT_SUCCESS` | 来源回测任务不存在或未成功 |
| 422，`BACKTEST_JOB_RUN_MODE_FORBIDDEN` | 来源任务是 `quick` / `trial` / `selection_only`，不允许作为 selection 来源 |
| 404，JOB_NOT_FOUND | `job_id` 拼写错误或 UUID 不对 |
| 启动时报 FastAPI/Header 等断言 | 以仓库当前 `app.py` 为准；已修复「可选 Header 与 `=` 默认」的写法 |
| 任务永远 `queued` | 已起 **worker** 则会被领取；未起 worker 时一直 `queued` 属正常 |

---

## 八、与阶段 D（worker）的关系

- **HTTP 服务**只负责建任务/查表；**是否执行算子**由另进程的 **worker** 完成。  
- 已提供首版 worker 后，任务会经 `queued` → `running` → `success` 或 `failed`；`GET` 同路径可查最终状态。  
- 若进程在 `running` 中被强杀，行可能**卡在** `running`，需人工 SQL 或后续做超时回收（本版不含）。

---

## 九、相关路径速查

| 路径 | 说明 |
|------|------|
| `run_pipeline_job_api.py` | 从仓库根启动 HTTP 服务 |
| `src/pipeline_job_api/app.py` | 路由、探针、错误体 |
| `src/pipeline_job_api/job_store.py` | 建 job、冪等、按 `public_id` 查询；含 **claim** / 终态回写 |
| `sql/migrations/005_factor_pipeline_job.sql` | 表与索引定义 |

---

## 十、Pipeline job worker（阶段 D，首版）

1. **前置**  
   与 API 使用**同一**根 `config.ini` / `config_dev.ini`；库表 `factor_pipeline_job` 已建；`factor_engine` / 行情 / `stock_daily` 等配置满足一次完整试跑（与手跑命令相同要求）。

2. **执行单条（领一条、跑完即退出，无任务则不做任何事）**  

   ```text
   python run_pipeline_job_worker.py --config config_dev.ini --once
   ```

   也可直跑模块（已自动把 `src` 加入路径，避免 `No module named 'common'`）：  
   `python src/pipeline_job_worker/worker.py --config config_dev.ini --once`  
   推荐仍用 **`run_pipeline_job_worker.py`**，便于同一套 `setup_logger` 写入 `logs/pipeline_job_worker_*.log`。

3. **常驻轮询（无任务时休眠）**  

   ```text
   python run_pipeline_job_worker.py --config config_dev.ini --loop --interval 30 --running-timeout-minutes 30
   ```

4. **行为摘要**  
   - 用 ``SKIP LOCKED`` 领取最早 `queued` → 置 `running` 并设 `started_at`。  
   - 同一 `job` 内 **`universe` 与 `backtest` 实证域**取 **job.test_universe**，空则回退根配置 `[backtest].test_universe` 或 `[factor_engine].universe`（经 `normalize_universe_code`）。  
   - 然后 **`run_factor_engine` → `run_backtest_io`**（均传 `factor_ids_override` / 域覆写）。  
   - 成功：`status=success`，`result_summary` 为 JSON 摘要；失败：`status=failed`，`error_message` 含堆栈。  

5. **`run_mode` 与 `ALLOW_FULL`**  
   - `new_only` / `quick` / `revalidate` / `trial`：按上执行。  
   - `full`：必须本进程环境变量 **`ALLOW_FULL=1`**，否则本 job 记 `failed` 并写原因。  
     - 产品建议：**普通前端不开放 `full`**；仅管理员/运维入口可触发，并保留 API 侧 `X-Allow-Full-Run: 1` 防护。  
  - `selection_only`：仅执行 `selection_and_store`（准入判定），**不包含** `new_only` 计算/回测阶段；必须提供 `backtest_job_id`，且来源任务需为 `new_only/revalidate/full` 的 `success`。  

6. **running 超时回收（已启用）**  
   - 每轮 worker 在 `claim` 前会执行一次回收：`status=running AND started_at <= now()-阈值` 的任务，重置回 `queued`。  
   - 阈值参数：`--running-timeout-minutes`（默认 **30**）。  
   - 目的：避免进程异常退出后任务永久卡在 `running`。

7. **与 API 同时开**  
   终端 A：`run_pipeline_job_api.py`；终端 B：`run_pipeline_job_worker.py --loop`。先 `POST` 建任务，再观察 worker 日志与 `GET` 状态。

8. **worker 日志在哪里**  
   - 终端会打印**阶段说明**（如 `【1/2】run_factor_engine`、成功/失败分隔线、耗时秒数）。  
   - 同目录下按日期落盘：**`logs/pipeline_job_worker_YYYYMMDD.log`**（与 `common.utils.setup_logger` 行为一致）。  
   - 因子/回测内部细节另见 **`logs/factor_engine_runner_*.log`**、**`logs/backtest_io_runner_*.log`**。
