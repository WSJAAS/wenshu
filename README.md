# 文枢 Wenshu

**当前版本：v2.0.0（二次开发版本）**

本地跑的长篇小说创作工具。写设定、捋大纲、生成章节、改文风，都在一个页面里。

不用登录，不用 Docker，数据全在你自己电脑上。

> 本项目是基于上游开源作品的**二次开发版本**，使用独立的自有版本号（2.0.0），
> 与上游项目的版本序列无关。界面已去除上游的推广、赞助与旧版本号展示，
> 不再连接任何上游托管服务。上游署名依据许可证要求保留。
> 具体改动见 [CHANGES.md](CHANGES.md) 第六节。

---

## 它能干什么

**写**
- 作品、人物、大纲、章节、伏笔、关系图，一套齐
- 设定一次写好，后面生成章节时自动带上

**改**
- 去 AI 味：把「他感到十分疲惫」改成「他眼皮沉得像灌了铅」
- 体检：给文本打分，指出哪句是套话、哪里缺感官描写
- 改完给你候选稿，**原稿不动**，你看过觉得行才采纳

**查**
- 让助手并发检查好几章的一致性，查出人物年龄对不对、时间线顺不顺
- 每条问题都标出处，说清是哪一章哪句话

---

## 为什么做这个

市面上的 AI 写作工具要么强制登录、要么数据在别人服务器、要么得开好几个软件来回倒文本。

这个工具的想法很简单：**一个本地页面，写完直接导出，中间不用倒腾。**

具体做法：

- **原稿永远安全。** 改写只产出候选稿，采纳才落盘，随时能退回上一版。
- **任务各算各的。** 同时处理两篇稿子不会串——每篇有自己的任务号，还按内容指纹校验。
- **改到一半能看。** 并发检查多章时进度实时往外出，不用干等。
- **失败不拖累别人。** 三个任务里挂一个，另外两个照常出结果。

---

## 装

需要 Python 3.12+ 和 Node.js 20+。数据库用 SQLite，不用另装。

### 1. 后端

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

### 2. 配置

```powershell
copy backend\.env.example backend\.env
```

打开 `backend\.env`，填上模型信息：

```ini
OPENAI_API_KEY=你的密钥
OPENAI_BASE_URL=https://你的端点/v1
DEFAULT_MODEL=你的模型名
```

**不填也能跑。** 改文风和体检走本地规则，不需要模型。只有生成章节和让助手干活才要。

### 3. 建库

```powershell
cd backend
..\.venv\Scripts\python.exe -m alembic -c alembic-sqlite.ini upgrade head
```

### 4. 前端

```powershell
cd frontend
npm install
npm run build
```

打包结果直接丢进 `backend/static`，后端一起伺服。

### 5. 启动

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000

> **注意工作目录必须是 `backend/`。** 配置是按相对路径读的，从别处启动读不到。

或者直接用 `tools\start.bat`。

---

## 命令行

不想开浏览器就用这个：

```powershell
novelctl status                    # 看看跑没跑起来
novelctl projects                  # 有哪些作品
novelctl new "书名" --genre 悬疑    # 建一个
novelctl chapters <项目ID>          # 看章节
novelctl export <项目ID> -o 稿.txt  # 导出

novelctl polish -f 稿.txt -o 改后.txt   # 改文风，原稿不动
novelctl accept -f 改后.txt              # 采纳
novelctl rollback                        # 退回上一版

novelctl diagnose -f 稿.txt              # 体检
```

加 `--json` 出结构化结果，方便脚本调。

---

## 目录

```
wenshu/
├── backend/     后端（FastAPI + SQLite）
├── frontend/    界面（React + Vite + Ant Design）
├── engine/      改文风和体检的引擎，可以单独用
├── tools/       启动脚本、命令行、测试
├── docs/        说明文档
└── contrib/     第三方参考代码（只读，不参与运行）
```

---

## 怎么跑测试

```powershell
# 子代理模块
.\.venv\Scripts\python.exe tools\test_subagent.py
.\.venv\Scripts\python.exe tools\test_subagent_stream.py
.\.venv\Scripts\python.exe tools\test_subagent_stage3.py

# 端到端（需要服务先跑起来）
.\.venv\Scripts\python.exe tools\accept_e2e.py
```

---

## 许可证

**AGPL-3.0**

这个项目是把两个开源作品合起来的：

- [MuMuAINovel](https://github.com/xiamuceer-j/MuMuAINovel)（GPL-3.0）→ `backend/` `frontend/`
- baibaiAIGC（AGPL-3.0）→ `engine/`

按 GPLv3 第 13 条，GPL 作品可以和 AGPL 作品合并，合并后整体算 AGPL。所以这个项目整体是 AGPL-3.0。

**这意味着**：你可以随便用、随便改。但如果你拿它开网站给别人用，得把源码给用户。

`contrib/` 里那份 AI-Novel-Assistant 有商业授权条款，只放在那儿当资料看，不参与运行。别拿它商用。

第三方依赖清单见 [THIRD_PARTY.md](THIRD_PARTY.md)，改了什么见 [CHANGES.md](CHANGES.md)。
