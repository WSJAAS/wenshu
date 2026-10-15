# 改动说明

依据 **AGPL-3.0 §5(a)**，此处逐项标注本工程相对上游的修改内容与日期。

修改日期：**2026-10-13**

---

## 一、相对 MuMuAINovel（GPL-3.0）

### 1.1 `backend/app/config.py` — 数据目录可配置

**改动**：新增 `load_dotenv` 显式加载，新增 `MUMU_DATA_DIR` 环境变量。

**原因**：

1. 该模块顶部的 `DATABASE_URL` 用 `os.getenv` 读取，早于 pydantic 实例化
   `Settings` 的时机。`pydantic` 只在实例化时读 `.env`，所以模块级读取
   拿不到 `.env` 的值，会静默退回默认值。需在顶部显式加载。
2. 原 `DATA_DIR` 固定在代码目录内的 `backend/data`，更新代码会影响已有数据。

**改动后**：数据目录可用 `MUMU_DATA_DIR` 指向代码目录之外。

### 1.2 `backend/app/services/memory_service.py` — 向量记忆路径锚定

**改动**：`chroma_dir` 由相对路径 `"data/chroma_db"` 改为 `DATA_DIR / "chroma_db"`。

**原因**：原实现是相对路径，落点取决于进程工作目录。从不同目录启动会指向
另一套空库，表现为「重启后作品记忆消失」。

### 1.3 `backend/app/main.py` — 注册润色路由

**改动**：在路由导入与注册列表中补入 `polish`。

**原因**：`app/api/polish.py` 已实现但从未注册，接口实际不可达。
原前端 `App.tsx` 中 `Polish` 页面与路由亦被注释，属上游未完成的功能。

### 1.4 `frontend/src/App.tsx` — 启用润色路由

**改动**：取消 `Polish` 导入与 `<Route path="polish">` 的注释。

### 1.5 `frontend/src/pages/Polish.tsx` — 新增润色工作台页面

**改动**：新增文件。上游引用了该组件但文件不存在。

实现要点：原文与候选稿左右分栏对照、诊断指标展示、人工采纳、版本回退。

### 1.6 `frontend/src/pages/ProjectDetail.tsx` — 菜单项

**改动**：补入「润色」菜单项与选中态判断（上游同样被注释）。

### 1.7 无登录改造

**改动文件**：

- `backend/app/middleware/auth_middleware.py`
- `frontend/src/components/ProtectedRoute.tsx`
- `frontend/src/App.tsx`

**原因**：本工程定位为本地单人使用，不需要多用户鉴权。

详见 [docs/无登录改造.md](docs/无登录改造.md)。

### 1.8 品牌更名

**改动**：项目名、界面标题、显式文案由原名改为「文枢 / Wenshu」。

**依据**：AGPL-3.0 §5 允许修改，§7(e) 允许附加条款但不得误标来源。
本次更名已保留原版权声明与许可证全文，不暗示原作者认可。

### 1.9 子代理调度

**改动文件**：

- `backend/app/services/subagent_contract.py`（新增）
- `backend/app/services/subagent_executor.py`（新增）
- `backend/app/services/project_agent_tools.py`（注册 `dispatch_subagents`）
- `backend/app/services/project_agent_service.py`（新增流式分支与快照构建器）
- `frontend/src/components/project-agent/ProjectAgentPanel.tsx`（见下）

**原因**：上游的 Agent 是单 agent + 工具循环（`MAX_TOOL_ROUNDS = 4`），
每次工具执行都是 `await registry.execute()` 完成后才发一次更新，
执行期间时间线静止。批量检查跨章一致性时既慢又看不到进度。

**改动内容**：

1. 新增只读子任务执行模块：有界并发、可取消、受整轮预算约束。
2. 子任务不持有数据库会话、写工具与 MCP 工具，
   只接收主流程固化的文本快照。
3. 新增 `_dispatch_subagents_stream` 流式分支，
   在执行期间逐个子任务转发 `step_start` / `step_update`。
4. 预算从第一版生效，重复派发与重试共享同一份整轮额度。

**前端改动（`ProjectAgentPanel.tsx`）**：

- `onStepUpdate` 由上游的 `map` 改为真正的 upsert 并按 `sequence` 排序。
  上游实现对**不存在**的 `step.id` 不做任何处理，
  因此缺少 `step_start` 的步骤会被静默忽略，进度永远不显示。
- 新增 `subagent` 步骤的图标与分类名。
- 新增子任务详情渲染：任务目标、当前阶段、失败原因、
  发现项与其来源依据、未检查项。

**未调整**：`MAX_TOOL_ROUNDS` 仍为 4。
一次批量派发算当前轮中的**一次**工具调用，不消耗额外轮次。

---

## 二、相对 baibaiAIGC（AGPL-3.0）

### 2.1 `engine/local_rewriter.py` — 原样引入

未修改。提供去 AI 痕迹的规则改写能力，仅依赖标准库。

### 2.2 `engine/text_diagnostics.py` — 原样引入

未修改。提供文本体检指标。

### 2.3 `engine/aigc_round_service.py` — 新增任务隔离

**改动**：新增 `compute_input_fingerprint()`；进度负载新增 `input_fingerprint` 字段；
在 `_load_progress_payload()` 中增加指纹校验分支。

**原因**：原实现存在串稿缺陷。两个条件叠加：

1. Web 端使用固定任务路径 `tmp_id = f"web_{mode}"`，不同稿件共用同一进度文件；
2. `chunk_id = f"p{paragraph_index}k_c{chunk_index}"` 只由段落／分块**位置**生成，
   与文本内容无关，而进度复用时只校验 `chunk_id` 是否存在于 manifest，不校验内容。

于是乙稿的 `p0_c0` 命中甲稿旧进度被直接跳过，乙稿读到甲稿的结果。

**改动后**：进度文件记录输入内容的 SHA256 指纹，指纹不匹配则整体作废旧进度。
同一稿件重跑仍正常续跑。

### 2.4 `engine/` 其他文件 — 原样引入

`chunking.py`、`llm_client.py`、`app_config.py`、`aigc_records.py`、`prompts/`
均原样引入，未作修改。

---

## 三、相对 AI-Novel-Assistant（AGPL-3.0 + 商业双许可）

**未作任何修改**。原样保留于 `contrib/ai-novel-assistant/`，
仅供本地研读参考，其商业授权条款依然有效。

---

## 四、未包含的上游内容

以下内容**未**纳入本工程：

| 内容 | 原因 |
| :--- | :--- |
| `backend/embedding/`（449 MB ONNX 模型） | 首次启动自动下载并校验，不入库 |
| `backend/static/` | 构建产物，由 `npm run build` 生成 |
| `backend/data/`、`logs/` | 运行时数据 |
| `node_modules/` | 由 `npm install` 重建 |
| MuMuAINovel 的商业／社区模块 | 首期不需要 |

---

## 六、界面去推广与自有版本号

修改日期：**2026-10-14**

本工程为二次开发版本，使用自有版本号 **2.0.0**，与上游版本序列相互独立。

### 6.1 移除右上角推广位

**改动文件**：

- `frontend/src/pages/ProjectList.tsx`

**改动**：移除头部右上角的公告铃铛入口（桌面端与移动端各一处），
以及拉取上游云端公告的 `useAnnouncements` 调用与 `AnnouncementTimelineModal` 渲染。

**原因**：该公告内容由上游托管服务器下发，属于外部推广位，
在二次开发版本中不保留。

### 6.2 移除中间赞助位

**改动文件**：

- `frontend/src/components/AppFooter.tsx`
- `frontend/src/pages/Sponsor.tsx`（删除）
- `frontend/src/pages/ProjectDetail.tsx`
- `frontend/src/App.tsx`

**改动**：

1. 页脚不再渲染「赞助」「赞助支持」按钮与其跳转地址。
2. 删除赞助页 `Sponsor.tsx`，移除 `/project/:id/sponsor` 路由。
3. 项目详情侧边菜单移除「赞助支持」项，
   默认落点由 `sponsor` 改为 `world-setting`。

### 6.3 移除旧版本号与上游外链

**改动文件**：

- `frontend/src/components/AppFooter.tsx`
- `frontend/src/services/versionService.ts`
- `frontend/src/config/version.ts`

**改动**：

1. 页脚不再显示上游版本号与 `LinuxDO 社区` 外链。
2. `versionService` 不再联网请求上游 GitHub release badge，
   改为直接回显自有版本号，避免把上游版本误报为「新版本」。
3. 保留上游项目名与仓库地址，其性质为许可证要求的署名，非推广入口。

### 6.4 二次开发声明与自有版本号

**改动文件**：

- `frontend/src/config/version.ts`
- `frontend/package.json`
- `backend/app/config.py`
- `backend/app/__init__.py`

**改动**：

1. 新增 `SECONDARY_DEV_VERSION = '2.0.0'` 与 `secondaryDevNotice` 字段。
2. 页脚显式标注「本项目为二次开发版本」及上游来源。
3. `package.json` 版本号改为 `2.0.0`（构建时注入前端）。
4. 后端 `app_name` 改为 `Wenshu`，`app_version` 改为 `2.0.0`。

### 6.5 移除其他外部推广入口

**改动文件**：

- `frontend/src/pages/Settings.tsx`
- `frontend/src/services/changelogService.ts`
- `frontend/src/pages/Login.tsx`、`frontend/src/pages/AuthCallback.tsx`
- `frontend/src/components/SpringFestival.tsx`（删除）及 `SpringFestival.css`（删除）

**改动**：

1. 设置页三处「免费注册」跳转按钮移除，
   相关渠道选项改为「自定义渠道（OpenAI 兼容）」，由用户自行填写地址与凭据。
2. 更新日志数据源由上游仓库改为本工程仓库。
3. 登录页与 OAuth 回调页的欢迎公告弹窗移除。
4. 移除 `App.tsx` 中挂载的春节装饰组件。

### 6.6 后端默认不再连接上游托管服务

**改动文件**：

- `backend/app/config.py`
- `backend/app/services/announcement_client.py`
- `backend/app/services/workshop_client.py`
- `backend/app/services/cover_generation_service.py`

**改动**：

1. `WORKSHOP_CLOUD_URL` 默认值由上游托管地址改为空字符串。
2. 两个云端客户端在地址为空时直接抛出明确错误，
   不再构造指向上游的请求。
3. 封面图生成不再内置上游默认地址，必须由用户显式配置。

**影响**：提示词工坊的云端共享功能默认关闭。
如需使用，在 `backend/.env` 中设置 `WORKSHOP_CLOUD_URL` 指向自建服务端。

### 6.7 清除其余上游品牌残留

**改动文件**：

- `backend/app/api/auth.py`、`backend/app/api/settings.py`
- `backend/app/api/changelog.py`
- `backend/app/services/project_agent_service.py`
- `backend/app/services/onnx_embedding.py`
- `backend/app/config.py`、`backend/app/models/settings.py`、`backend/app/schemas/settings.py`
- `backend/app/security.py`
- `frontend/src/pages/SystemSettings.tsx`
- `docker-compose.yml`、`backend/scripts/*`
- 前端若干 `.tsx` / `.ts`

**改动**：

1. 邮件模板（注册／登录／重置密码／SMTP 测试）与发件人名称
   由上游品牌改为「文枢」。
2. 更新日志 API 数据源由上游仓库改为本工程仓库。
3. 创作助手的系统提示词与界面文案去掉上游品牌名。
4. ONNX 模型缓存目录与下载 UA 改为 `Wenshu`。
5. 容器名、镜像名、数据库名／用户名默认值、会话密钥默认值改为自有命名。
6. 公告编辑器中的第三方推广图与文案（作为默认模板）替换为本地占位内容。

**保留**：`README.md`、`CHANGES.md`、`THIRD_PARTY.md` 与
`frontend/src/config/version.ts` 中的上游项目名与仓库地址，
其性质为 AGPL 要求的来源署名。

---

## 八、交接后拆书静默失败的修复（2026-10-14 晚）

修改日期：**2026-10-14**

用户现象：浏览器脚本交接后「用户端没有反应」。实测复盘（wenshu.log，
UTC 时间 +8 即北京时间）：

- 22:12 后台服务启动（`--log-level warning`）
- 22:13 交接《我不是戏神》：抓取成功（完整 10/30 章），
  自动交接成功，**拆书管线在 AI suggestion 成功后 35ms 崩溃**
  （`'NoneType' object has no attribute 'strip'`），
  拆书任务静默 failed，项目从未创建
- 22:15 交接《父皇他两辈子都在装》：成功建项目

### 8.1 崩溃根因：取源 TXT 的附录被当成章节

`results_to_txt` 把未抓取章的清单（几百行「第N章 …（not_fetched）」）
附在正文后。拆书的章节切分器（`STRONG_CHAPTER_PATTERNS` +
弱标题启发）把这些行识别为章节标题：30 章被切成 291 章，
其中 60+ 个空章节。空章的 `summary` 为 None，
`_build_fallback_outline_structure` 里
`(chapter.summary or self._build_summary(...)).strip()` 在两者皆空时
对 None 调 `.strip()` 崩溃。

### 8.2 修复一：附录隔离（fanqie_source.py）

`results_to_txt` 的抓取说明区改用成对标记
`〖来源附录开始〗` / `〖来源附录结束〗` 包裹。

### 8.3 修复二：切分前剥附录（txt_parser_service.py）

新增 `strip_source_appendix()`：按成对标记整段剥掉附录区，
没有标记的旧 TXT 原样返回（向后兼容）。
`_run_pipeline` 在 `clean_text` 后调用它。

同时收紧 `_is_weak_heading`：以破折号、省略号、中文引号开头/收尾的
正文短行（拟声词「轰隆——」、引语「“父皇——”」）不再判为章节标题。
实测《我不是戏神》从 91 章（仍含大量假章节）收敛到 38 章。

### 8.4 修复三：空章兜底（book_import_service.py）

`_build_fallback_outline_structure` 对空章返回占位摘要，
不再对 None 调 `.strip()`；`_build_summary` 对纯空白内容返回 None
（原语义），由调用方兜底。

### 8.5 修复四：拆书终态回写取源任务（api/source.py）

新增 `_await_book_import_result()`：自动交接后盯拆书任务到终态
（默认 600s），失败时把 `import_status` 置 `failed`、
`import_message` 写明原因。此前 `import_status` 停在 `handed`，
拆书内部崩溃用户完全看不到 —— 这就是「没有反应」的直接表现。

### 8.6 修复五：日志保留异常堆栈（logger.py）

`UvicornFormatter.format()` 只取 `record.getMessage()`，
`exc_info` 堆栈被丢弃，ERROR 行后永远没有 Traceback
（本次排障实测绕了远路）。现在 format 时附带堆栈文本。

### 8.7 验证

- 用任务 9e68bfee（30 章原崩溃数据）重放交接：
  拆书 completed，38 章 / 38 大纲，AI 全程使用
- 6 章免费范围重放：`import_message` 正确写回
  「抓取完成后自动交接：完整 6 章，共 14536 字 拆书解析完成（100%）」
- 全部 8 个验收脚本 PASS：
  accept_source_to_import / accept_handover / accept_e2e /
  accept_isolation / accept_truncation / accept_ai_visibility /
  accept_frontend_source / accept_source_e2e

---

## 十、整本下载（外部下载器集成）

修改日期：**2026-10-15**

### 背景

番茄小说网页端对未登录访问只开放每本书前 10 章全文，其后章节
服务端直接截断为约 200 字符试读（实测《惹金枝》第 11 章 171 字，
申报 2088 字）。锁定章全文只对 APP 端免费开放，网页端需要 SVIP。

书源模块的直连路径受此限制；浏览器代抓通道也拿不到（服务端
对无 SVIP 登录态一律截断）。整本全文因此交给专门的外部工具：
zhongbai2333/Tomato-Novel-Downloader（Rust，MIT），
它通过官方 App 接口抓取，可拿到全部章节全文。

### 新增文件

- `backend/app/services/ext_downloader.py` —— 外部下载器封装：
  进程拉起/复用（探活 + 双重检查锁）、任务提交、进度查询、
  EPUB 定位、EPUB→TXT 转换
- `backend/app/api/ext_download.py` —— 三条路由：
  `POST /api/ext-download/start`（发起）、
  `GET /api/ext-download/status/{job_id}`（进度）、
  `GET /api/ext-download/result/{job_id}`（导出 TXT）

### 改动文件

- `backend/app/config.py`：新增 `EXT_DOWNLOADER_PATH` /
  `EXT_DOWNLOADER_DATA_DIR` / `EXT_DOWNLOADER_ADDR` 三项配置。
  注意默认值经 `PROJECT_ROOT.parent` 定位（runtime 在仓库根，
  而 PROJECT_ROOT 是 backend/）
- `backend/app/main.py`：注册 ext_download 路由
- `frontend/src/types/index.ts`：`ExtDownloadProgress` /
  `ExtDownloadJob` / `ExtDownloadStatus` 类型
- `frontend/src/services/api.ts`：`extDownloadApi`（start / getStatus / resultUrl）
- `frontend/src/pages/Source.tsx`：在线书源页新增「整本下载」卡片，
  含 ID/链接解析、任务发起、2 秒轮询、进度条、TXT 导出按钮

### 外部工具

- 可执行文件：`runtime/tools/tnd/TomatoNovelDownloader.exe`（v2.4.15，
  Windows x64，GitHub Release 下载，未入 git）
- 以 `--server` 模式常驻 `127.0.0.1:18423`，输出 EPUB 落在
  `runtime/data/ext_downloader/`（子进程 CWD 即输出目录）
- 与书源共用 `FANQIE_ENABLED` 开关：关掉它两者一起消失

### 关键设计

1. **复用优先**：已运行的实例直接复用（用户可能正用它下载别的书），
   探活用业务接口而非 TCP —— 端口被占但服务没起来时只有真发请求才分得清。
2. **子进程 CWD = 输出目录**：该工具 `save_path` 为空时把产出写到
   进程工作目录；实测改 config.yml 的 save_path 不可行（配置只在
   启动时读一次），所以直接把 CWD 定到数据目录，产出不散落。
3. **任务状态不落库**：状态由工具自持（内存 + done_retention_ms），
   本模块只做薄封装，不制造第二份真相。
4. **EPUB→TXT 在服务端做**：按 `chapter_(\d+)` 数字排序（字典序会把
   第 10 章排到第 2 章前）；先删 `<head>`/`<style>`/`<script>` 再去标签
   （否则 CSS/JS 源码进正文）；章节标题精确去重（`<head><title>` 与
   `<body><h1>` 各打一遍）。
5. **中文文件名走 RFC 5987**：`filename*=UTF-8''<百分号编码>`，
   ASCII 兜底名 —— HTTP 头只能装 latin-1，直接写中文会抛
   `UnicodeEncodeError`。

### 实测（accept_ext_download.py，全 PASS）

- 《惹金枝》550 章整本：56 秒跑完，550/550 全文
- 导出 TXT 1,201,672 字；第 11 章 2097 字（对照未登录网页端 171 字）
- EPUB 2.1 MB 落在 `runtime/data/ext_downloader/`
- 重复发起正常排队；空 book_id 返回 400；未完成导出返回 409

---

## 十一、恢复办法

本工程保留了对上游的引用方式，如需比对原始实现，
可在 `docs/上游对齐.md` 中查到各文件的来源与差异摘要。
