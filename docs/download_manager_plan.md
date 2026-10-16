# 下载管理器 ↔ 拆书页面联动实施方案

> 主会话工程审计与规划产物。实施由子代理（yvli/deepseek4.1f）按本文件执行。
> 日期：2026-10-15　范围：wenshu（文枢）二开工程

---

## 一、工程审计结论（现状）

### 既有链路

| 模块 | 位置 | 状态 |
| :--- | :--- | :--- |
| 拆书页面 | `frontend/src/pages/BookImport.tsx`（挂 `/?view=book-import`，由 `ProjectList.tsx` 渲染） | 只接受手动上传 TXT File；任务状态 1.5s 轮询；sessionStorage 缓存 `book_import_page_cache_v1` |
| 拆书后端 | `backend/app/api/book_import.py` + `services/book_import_service.py` | `create_task(user_id, filename, file_content, ...)` 内存任务；SSE apply/retry 流 |
| 书源页面 | `frontend/src/pages/Source.tsx` | 逐章取源任务持久化（`source_tasks` 表），**已有→拆书自动交接样板**：`import_task_id/import_status/import_message` 字段 + `_await_book_import_result()` 盯终态回写 |
| 整本下载后端 | `backend/app/api/ext_download.py` + `services/ext_downloader.py` | 薄封装外部 Rust 下载器（TomatoNovelDownloader，`127.0.0.1:18423`）；**任务状态不落库**（"不做第二份真相"）；EPUB→TXT 转换现成（`epub_to_txt`） |
| 整本下载前端 | `Source.tsx` 内嵌卡片（约 509–618 行） | 单任务输入框 + 轮询；完成后**只有「下载 TXT」按钮**，提示「导出后可在拆书模块上传该 TXT 做导入」 |

### 断点（本方案要消灭的）

1. 整本下载完成 → 用户须手动另存 TXT → 切到拆书页 → 手动上传。中间两次人工搬运，120 万字 TXT 走浏览器 round-trip。
2. 下载任务无管理界面：单任务、无列表、无历史、页面刷新即丢 `extJobId`（存于 React state）。
3. 拆书页对下载侧零感知；下载侧对拆书侧零感知。

### 可复用资产

- `source.py` 的交接模式：`_hand_to_book_import()` + `_await_book_import_result()` + `import_*` 字段三件套（**本方案照抄此模式**）。
- `ext_downloader.epub_to_txt()`：EPUB→TXT 现成，转换 <1s，不缓存 TXT 文件、现场重转即可。
- 建表惯例：lifespan 里 `Model.__table__.create(sync_conn, checkfirst=True)` + 幂等补列函数（见 `main.py:55` `_ensure_source_task_columns`），**不走 alembic**。
- 门控惯例：`FANQIE_ENABLED` 开关，关闭时 403（`_require_enabled()`）。
- 前端联动惯例：`eventBus`（`store/eventBus.ts`）+ 页面内 `setInterval` 轮询。

---

## 二、目标

1. **下载管理器**：多任务列表（进行中+历史）、逐任务进度/状态/字数、取消、下载 TXT、交给拆书、重新交接。替代 Source 页的单任务输入框卡片。
2. **直接接入拆书页面**：下载完成自动（或一键）交给拆书管线，不经过浏览器下载/上传。
3. **两边联动**：
   - 下载侧 → 拆书侧：done 后自动交接（auto_import 默认开），下载管理器里实时显示拆书任务状态链（交接→解析中 N%→可预览/失败）。
   - 拆书侧 → 下载侧：拆书页新增「从下载管理器导入」面板，列出已完成的下载任务（含其拆书状态），一键导入此书，直接挂进现有拆书流程；已交接的可一键打开对应拆书任务。
4. **拆书页面实时更新状态**：面板 3s 轮询下载任务列表；下载管理器列表同样实时；拆书任务沿用现有 1.5s 轮询。

---

## 三、后端实施（子代理 A）

### 3.1 新表 `download_jobs`

在 `backend/app/api/ext_download.py` 内定义（照抄 `SourceTask` 模式，不放 `app/models/`，与 `source_tasks` 保持同一组织方式）：

```python
class DownloadJob(Base):
    __tablename__ = "download_jobs"
    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(100), nullable=False, index=True)
    book_id = Column(String(32), nullable=False, index=True)
    ext_job_id = Column(String(64), default="", index=True)   # 外部下载器任务号（其 job 表 id，可能是 int，统一存 str）
    state = Column(String(20), nullable=False, default="queued", index=True)
    # queued / running / done / error / cancelled / lost
    message = Column(String(500), default="")
    error = Column(Text, default="")
    book_title = Column(String(300), default="")
    book_author = Column(String(200), default="")
    chapter_total = Column(Integer, default=0)
    saved_chapters = Column(Integer, default=0)
    group_done = Column(Integer, default=0)
    group_total = Column(Integer, default=0)
    epub_path = Column(String(500), default="")
    epub_bytes = Column(Integer, default=0)
    txt_chars = Column(Integer, default=0)
    # ---- 拆书交接（与 source_tasks 同款三件套 + auto_import）----
    import_task_id = Column(String(36), default="")
    import_status = Column(String(20), default="")    # "" / handed / failed
    import_message = Column(String(500), default="")
    imported_at = Column(DateTime, nullable=True)
    auto_import = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 3.2 lifespan 注册（`backend/app/main.py`）

照 `_ensure_source_task_columns` 的模式，在现有 source 建表块之后追加：

- `DownloadJob.__table__.create(sync_conn, checkfirst=True)`
- `_ensure_download_job_columns(sync_conn)` 幂等补列（列集合 = 上表全部非主键列；先查现有列、缺什么补什么）
- **重启不改状态**：与 source 任务不同——下载的执行者是独立外部进程，后端重启不影响它。DB 里 `running` 的 job 留给首次惰性同步恢复真实状态。只有「同步时外部下载器里查无此 ext_job_id」才标 `lost`。此差异必须在注释里讲清楚（对照 source 任务重启标 paused 的原因：其抓取协程随后端进程死）。

### 3.3 状态机与惰性同步

状态：`queued / running / done / error / cancelled / lost`。终态：`done / error / cancelled / lost`（终态不再同步）。

核心函数 `_sync_jobs(db, jobs)`（放在 ext_download.py）：

- 仅对非终态 job：批量一次 `ext_downloader_service.get_job()` 拉外部列表（注意现实现是每个 job 单独请求一次 `/api/jobs`；为省请求，同步器直接复用 `get_job`，活跃任务数量小可接受；注释说明）。
- 找到 → 更新 state/progress 四字段/title/author；外部 `state==done` 时调 `_locate_epub()` 定位产物，写 `epub_path/epub_bytes`；外部失败原因读 `message` 或 `error` 字段（现有 `download_status` 已示范）。
- 找不到（外部下载器重启丢内存态）→ `state=lost`，message 如实写：「外部下载器已重启或任务已过期被清理，任务状态丢失；若文件已落盘仍可尝试导出/交接」。
- **自动交接触发点**：同步导致 state 变为 `done` 且 `auto_import=True` 且 `import_status` 为空 → `asyncio.create_task(_auto_handoff(job_id, user_id))` 后台交接。交接协程自开 session（模式照 `source._run_task`），成功后写 `import_*` 并 `_await_book_import_result` 盯拆书终态回写（照抄 `source.py` 现成函数逻辑；可把这两个函数从 source.py 抽到共用位置或直接 import 复用，**优先 import 复用，不复制代码**——若 import 引入循环依赖再抽 `app/services/import_handoff.py`）。

### 3.4 新端点（挂在现有 `/ext-download` 路由上，全部 `_require_enabled()` 门控）

| 方法/路径 | 说明 |
| :--- | :--- |
| `POST /ext-download/jobs` | body `{book_id, title?, author?, auto_import=true}`。规范化 book_id（复用 `_normalize_book_id`，从 source.py 抽出或复用）；落库 DownloadJob(state=queued) → 调 `ext_downloader_service.download_book()` 拿外部 job id 写 `ext_job_id` → 立即做一次同步 → 返回 summary。外部失败：job.state=error + 原因，HTTP 502 detail 同现有风格 |
| `GET /ext-download/jobs?limit=30` | 先 `_sync_jobs`（仅非终态），再按 created_at desc 返回列表。响应字段 = DownloadJob 全字段摘要（见 3.5） |
| `GET /ext-download/jobs/{id}` | 单个（含同步） |
| `POST /ext-download/jobs/{id}/cancel` | 转发外部 `/api/jobs/<ext>/cancel`（尽力而为），state=cancelled |
| `POST /ext-download/jobs/{id}/to-book-import` | 手动交接。EPUB→TXT（现场 `epub_to_txt`）→ `book_import_service.create_task(user_id, filename=f"{title or book_id}.txt", file_content=txt.encode(), project_id=None, create_new_project=True, import_mode="append", extract_mode=query 参数默认 "full", tail_chapter_count=10)` → 写 `import_*` → 后台盯终态。**幂等**：`import_status=="handed"` 且拆书任务未终态时直接返回现有 import_task_id，不重复创建。`state=lost` 也允许尝试（EPUB 可能已落盘，找不到时 404 detail 如实说明）。返回 `{ok, import_task_id, txt_chars, ...}` |

**兼容**：现有 `POST /start`、`GET /status/{job_id}`、`GET /result/{job_id}` **原样保留不动**。

### 3.5 拆书侧端点（`backend/app/api/book_import.py` 新增）

`POST /book-import/tasks/from-download`：

- body（JSON）：`{download_job_id: str, extract_mode: str = "full", tail_chapter_count: int = 10}`
- 校验：`request.state.user_id`（无则 401，同现有端点）；`Depends(get_db)` 查 DownloadJob，不存在 404，user_id 不一致 403。
- job.state 必须是 `done`（或 `lost` 但 EPUB 找得到）；否则 409 detail 带当前 state。
- EPUB→TXT → `book_import_service.create_task(...)`（参数同 3.4 交接）→ 回写 DownloadJob 的 `import_*`（复用同一交接函数，保证两处口径一致）→ 返回 `{task_id, status, download_job_id}`。
- 幂等同 3.4。

### 3.6 后端自验

- `X:\小说工程\wenshu\.venv\Scripts\python.exe -c "from app.api import ext_download, book_import; from app.main import app"`（cwd=`backend/`，需 `PYTHONPATH=.` 或在 backend 下跑）导入无错。
- 新增 `backend/tests/test_download_manager.py`（若 `backend/tests/` 不存在则创建，配 `__init__.py`；用 FastAPI TestClient + monkeypatch `ext_downloader_service`），覆盖：
  1. 建表/补列幂等；
  2. 惰性同步：外部 done → DB done + epub 字段；外部查无 → lost；
  3. 交接幂等：重复 to-book-import 不重复建拆书任务；
  4. from-download：404/403/409 分支；
  5. `FANQIE_ENABLED=false` 时新端点 403。
- `pytest backend/tests/test_download_manager.py` 全绿。
- **不破坏**：`tools/accept_source_to_import.py` 涉及的现有函数签名一律不改。

---

## 四、前端实施（子代理 B）

### 4.1 类型（`frontend/src/types/index.ts`）

```ts
export interface DownloadJob {
  id: string;
  book_id: string;
  ext_job_id: string;
  state: 'queued' | 'running' | 'done' | 'error' | 'cancelled' | 'lost';
  message: string;
  error: string;
  book_title: string;
  book_author: string;
  chapter_total: number;
  saved_chapters: number;
  group_done: number;
  group_total: number;
  epub_path: string;
  epub_bytes: number;
  txt_chars: number;
  import_task_id: string;
  import_status: '' | 'handed' | 'failed';
  import_message: string;
  imported_at: string | null;
  auto_import: boolean;
  created_at: string | null;
  updated_at: string | null;
}
```

### 4.2 API（`frontend/src/services/api.ts`，扩展现有 `extDownloadApi`，保留现有 start/getStatus/resultUrl）

```ts
export const downloadApi = {
  jobs: (limit = 30) => api.get<unknown, DownloadJob[]>(`/ext-download/jobs?limit=${limit}`),
  job: (id: string) => api.get<unknown, DownloadJob>(`/ext-download/jobs/${id}`),
  start: (payload: { book_id: string; title?: string; author?: string; auto_import?: boolean }) =>
    api.post<unknown, DownloadJob>('/ext-download/jobs', payload),
  cancel: (id: string) => api.post<unknown, DownloadJob>(`/ext-download/jobs/${id}/cancel`),
  toBookImport: (id: string, extractMode: 'full' | 'tail' = 'full') =>
    api.post<unknown, { ok: boolean; import_task_id: string; txt_chars: number }>(
      `/ext-download/jobs/${id}/to-book-import?extract_mode=${extractMode}`),
  /** 拆书侧：从下载任务直接创建拆书任务 */
  bookImportFromDownload: (downloadJobId: string, extractMode: 'full' | 'tail' = 'full', tailChapterCount = 10) =>
    api.post<unknown, { task_id: string; status: string; download_job_id: string }>(
      '/book-import/tasks/from-download',
      { download_job_id: downloadJobId, extract_mode: extractMode, tail_chapter_count: tailChapterCount }),
};
```

### 4.3 eventBus（`store/eventBus.ts` 常量追加）

`DOWNLOAD_JOBS_CHANGED: 'downloadJobs:changed'`、`BOOK_IMPORT_TASK_CREATED: 'bookImport:taskCreated'`。

### 4.4 Source.tsx —— 整本下载卡片重构为「下载管理器」

- 保留顶部输入行（书籍 ID/链接 + 「下载整本」按钮），改调 `downloadApi.start`；去掉「同一时间只跑一个任务」文案与 `extPolling` 单任务限制（下载器自己排队），改为「下载器会排队执行，可同时提交多本」。
- 下方任务列表（进行中的排前，按 created_at desc）：每行 = 状态 Tag（queued 排队中/running 下载中/done 已完成/error 失败/cancelled 已取消/lost 状态丢失）+ 进度条（saved_chapters/chapter_total）+ 书名/作者 + 操作：
  - 非终态：「取消」
  - done：「下载 TXT」（沿用 `extDownloadApi.resultUrl(Number(ext_job_id), title)`）、「交给拆书导入」（`downloadApi.toBookImport`）
  - lost：允许「交给拆书导入」（EPUB 可能已落盘）
- 交接状态块：照抄现有取源任务的 import Alert 样式（`task.import_status === 'handed'` 那段，含拆书任务 3s 轮询 `bookImportApi.getTaskStatus(import_task_id)` 显示解析进度、失败可「重新交接」）。
- 列表轮询：存在非终态任务时每 3s `downloadApi.jobs()`；无活跃任务时停。交接动作后 emit `DOWNLOAD_JOBS_CHANGED`。
- 删除旧的 `extJobId/extStatus/extRunSeq/normalizeExtStatus` 单任务状态机（旧 `extDownloadApi.getStatus` 不再被本页使用）。

### 4.5 BookImport.tsx —— 「从下载管理器导入」面板（step 0）

- 位置：`currentStep === 0` 区块内、上传 Card 之下（或之上，子代理按视觉平衡定），默认折叠展开均可但默认展开。
- 数据：`downloadApi.jobs(20)`，3s 轮询（仅 step0 且组件挂载时）。
- 每行：书名/作者、下载状态 Tag、字数（txt_chars 或 epub_bytes 换算）、拆书状态：
  - 未交接 + done → 按钮「导入此书」→ `downloadApi.bookImportFromDownload(id)` → 成功后 `setTaskId(resp.task_id)`（现有 useEffect 自动接管状态轮询与 preview 拉取，步骤条自动进入「解析中」）→ emit `BOOK_IMPORT_TASK_CREATED`
  - 已交接（import_task_id 非空）→ 显示拆书状态 Tag + 按钮「打开该拆书任务」→ `setTaskId(import_task_id)`
  - 下载中 → 显示实时进度（saved/total 章），按钮禁用
- 面板顶部一行说明 + 「去下载管理器」链接 → `navigate('/?view=source')`。
- 不干扰现有上传/缓存/预览/导入任何逻辑；面板与上传 Card 互为独立入口。

### 4.6 前端自验

- `cd X:\小说工程\wenshu\frontend && npm run build` 通过（tsc 无错）。
- 不新增依赖；antd 组件用法照现有页面风格。

---

## 五、验收标准（主会话执行）

1. 后端 import 冒烟 + 新 pytest 全绿。
2. 前端 `npm run build` 通过。
3. `git diff` 审查：旧接口未改签名；`source.py` 仅被 import 复用、无行为变更（若抽了共用模块需重点审）。
4. 契约一致性：前端调用的每个路径/字段与后端实现对得上（逐一比对）。
5. 注释质量：关键决策点有中文「为什么」注释，符合项目风格。
