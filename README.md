# 文枢 Wenshu

本地 AI 长篇小说创作工作台。集创作底座与文本重塑引擎于一体，单机可跑，无需登录。

> **文枢**（wén shū）：文章的枢纽。取"枢"字意在它是创作流程的转轴——
> 设定、大纲、章节、润色、采纳、导出，都在同一处收束。

---

## 这是什么

一套**本地优先**的小说创作工具。把原先分散在两处的能力合并进同一个工程：

| 能力 | 来源 | 说明 |
| :--- | :--- | :--- |
| 创作底座 | MuMuAINovel（GPL-3.0） | 项目／人物／大纲／章节／伏笔／关系图 |
| 文本重塑引擎 | baibaiAIGC（AGPL-3.0） | 去 AI 痕迹、规则改写、文本体检、任务隔离 |
| 参考实现 | AI-Novel-Assistant（AGPL-3.0 + 商业双许可） | **仅本地研读，见 `contrib/` 说明** |

合并后整体以 **AGPL-3.0** 分发，见 [许可证](#许可证)。

---

## 特性

### 创作链路

建立项目 → 整理人物与设定 → 大纲 → 生成章节 → 对照润色 → 人工采纳 → 保存版本 → 导出

### 文本重塑引擎（`engine/`）

三个能力可独立调用，也可经界面使用：

- **去 AI 痕迹**（`local_rewriter.py`）：逐条规则表改写，输出完全由输入决定，不调用模型也能用。
- **文本体检**（`text_diagnostics.py`）：阅读体验评分、Show/Tell 比、感官词密度、套话检出。
- **任务隔离**（`aigc_round_service.py`）：按输入内容指纹区分任务，避免不同稿件互相复用结果。

### 无登录

本地单人使用，默认以本地管理员身份直接进入，无登录页、无 Cookie 校验。
如需恢复鉴权，见 [docs/无登录改造.md](docs/无登录改造.md)。

---

## 快速开始

### 环境

| 项 | 版本 |
| :--- | :--- |
| Python | 3.12+ |
| Node.js | 20+ |
| 数据库 | SQLite（内置，无需额外安装） |

### 一、装依赖

```powershell
# 后端
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

# 前端
cd frontend
npm install
```

### 二、配置

```powershell
copy backend\.env.example backend\.env
```

编辑 `backend\.env`，至少填写一组模型凭证：

```ini
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://你的端点/v1
DEFAULT_MODEL=你的模型名
```

> 不填也能启动：改造页与文本体检走本地规则，不依赖模型。

### 三、建库

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic -c alembic-sqlite.ini upgrade head
```

### 四、构建前端

```powershell
cd frontend
npm run build
```

产物直接落在 `backend/static`，由后端一并服务。

### 五、启动

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000

> **注意**：工作目录必须是 `backend/`。配置以相对路径读取 `.env`，
> 从别处启动会读不到配置。

---

## 命令行入口

`tools/novelctl.py` 把创作、润色、记忆收敛到一条命令，支持 `--json` 供脚本调用。

```powershell
novelctl status                    # 平台状态
novelctl projects                  # 列出作品
novelctl new "作品名" --genre 悬疑  # 建立作品
novelctl chapters <项目ID>          # 列出章节
novelctl export <项目ID> -o 稿件.txt # 导出

novelctl polish -f 稿件.txt -o 候选.txt  # 润色，产出候选稿不改原稿
novelctl accept -f 候选.txt              # 人工采纳
novelctl rollback                        # 版本回退
```

---

## 目录结构

```
wenshu/
├── backend/                 创作底座后端（FastAPI）
│   ├── app/api/             接口层（含 polish.py 去 AI 味）
│   ├── app/services/        业务服务
│   ├── app/models/          数据模型
│   └── alembic/sqlite/      SQLite 迁移
├── frontend/                前端（React + Vite + antd）
│   └── src/pages/           页面（含 Polish.tsx 润色工作台）
├── engine/                  文本重塑引擎（可独立调用）
│   ├── local_rewriter.py    去 AI 痕迹规则表
│   ├── text_diagnostics.py  文本体检与评分
│   ├── aigc_round_service.py 任务隔离与分块改写
│   └── prompts/             改写提示词
├── tools/                   启动脚本与 CLI
├── contrib/                 第三方参考件（仅本地研读）
├── docs/                    说明文档
└── licenses/                许可证全文
```

---

## 许可证

本项目合并了两个 GPL 系作品，依据 **GPLv3 §13**（与 AGPLv3 的兼容条款）
作为一个整体，以 **AGPL-3.0-only** 发布。

| 部分 | 来源 | 原许可证 |
| :--- | :--- | :--- |
| `backend/` `frontend/` | MuMuAINovel | GPL-3.0 |
| `engine/` | baibaiAIGC | AGPL-3.0 |
| `contrib/ai-novel-assistant/` | AI-Novel-Assistant | AGPL-3.0-only + 商业双许可 |

**这意味着**：

- 你可以自由使用、修改、分发；
- 若通过网络向他人提供服务，必须向其提供完整源码（AGPL §13）；
- 保留原作者版权声明，标明修改。

`contrib/ai-novel-assistant/` 仅作**本地研读参考**，其商业授权条款依然有效——
如需商用分发，须另行取得原作者授权。详见 [contrib/README.md](contrib/README.md)。

第三方组件清单见 [THIRD_PARTY.md](THIRD_PARTY.md)。

---

## 改动说明

本项目相对上游的修改见 [CHANGES.md](CHANGES.md)，
按 AGPL §5 要求逐项标注了修改内容与日期。
