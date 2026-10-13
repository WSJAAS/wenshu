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

## 五、恢复办法

本工程保留了对上游的引用方式，如需比对原始实现，
可在 `docs/上游对齐.md` 中查到各文件的来源与差异摘要。
