# 第三方组件与许可

本项目依据 **GPLv3 §13** 组合了两个 GPL 系作品。该条款明确允许
GPLv3 作品与 AGPLv3 作品链接为单一组合作品，组合后的整体适用 AGPLv3。

组合结果以 **AGPL-3.0-only** 发布。

---

## 组合进本体的部分

### 1. MuMuAINovel

| 项 | 内容 |
| :--- | :--- |
| 许可证 | GPL-3.0 |
| 原文 | [licenses/GPL-3.0.txt](../licenses/GPL-3.0.txt) |
| 用于 | `backend/`、`frontend/` |
| 上游 | 见仓库 `.git` 历史 |

### 2. baibaiAIGC

| 项 | 内容 |
| :--- | :--- |
| 许可证 | AGPL-3.0 |
| 原文 | [licenses/AGPL-3.0.txt](../licenses/AGPL-3.0.txt) |
| 用于 | `engine/` |

**GPLv3 §13 原文要点**：

> Notwithstanding any other provision of this License, you have permission to
> link or combine any covered work with a work licensed under version 3 of the
> GNU Affero General Public License into a single combined work, and to convey
> the resulting work. The terms of this License will continue to apply to the
> part which is the covered work, but the special requirements of the GNU
> Affero General Public License, section 13, concerning interaction through a
> network will apply to the combination as such.

即：GPLv3 作品与 AGPLv3 作品可以合法合并为单一作品，
合并后整体受 AGPL §13 的网络交互条款约束。

---

## 参考件（不并入本体）

### AI-Novel-Assistant

| 项 | 内容 |
| :--- | :--- |
| 许可证 | AGPL-3.0-only **＋商业双许可** |
| 原文 | [licenses/AI-Novel-Assistant-LICENSE.txt](../licenses/AI-Novel-Assistant-LICENSE.txt) |
| 声明 | [licenses/AI-Novel-Assistant-NOTICE.txt](../licenses/AI-Novel-Assistant-NOTICE.txt) |
| 位置 | `contrib/ai-novel-assistant/`（**原样保留，未作修改**） |

**重要约束**：该项目除 AGPL-3.0 外还有商业授权条款，
「以本服务或修改版本作为后端或核心服务，向第三方提供 SaaS、托管、代管类服务」
需要另行取得原作者商业授权。

因此本仓库对它的定位是：**仅本地研读参考，不并入本体、不分发其衍生服务**。
如需商用，须自行取得授权。详见 [contrib/README.md](../contrib/README.md)。

---

## Python 依赖

运行时依赖见 `backend/requirements.txt`。主要组件与其许可证：

| 组件 | 许可证 |
| :--- | :--- |
| FastAPI / Starlette / Uvicorn | MIT / BSD-3-Clause |
| SQLAlchemy | MIT |
| Alembic | MIT |
| Pydantic | MIT |
| ChromaDB | Apache-2.0 |
| onnxruntime | MIT |
| numpy | BSD-3-Clause |
| tokenizers | Apache-2.0 |
| LiteLLM | MIT（`engine/llm_client.py` 使用） |
| openai / anthropic SDK | MIT / Apache-2.0 |
| aiosqlite | MIT |
| cryptography | Apache-2.0 / BSD-3-Clause |
| psutil | BSD-3-Clause |
| json5 | Apache-2.0 |
| PyYAML | MIT |
| filelock | Unlicense |
| certifi | MPL-2.0 |

## 前端依赖

见 `frontend/package.json`。主要组件：

| 组件 | 许可证 |
| :--- | :--- |
| React / React-DOM | MIT |
| Ant Design | MIT |
| React Router | MIT |
| Axios | MIT |
| Zustand | MIT |
| dayjs | MIT |
| react-diff-viewer-continued | MIT |
| @xyflow/react | MIT |
| dagre | MIT |
| canvas-confetti | ISC |
| Vite | MIT |
| TypeScript | Apache-2.0 |

---

## 嵌入的模型

本项目使用 ONNX 格式的句向量模型用于长篇记忆检索：

| 项 | 内容 |
| :--- | :--- |
| 模型 | `paraphrase-multilingual-MiniLM-L12-v2` |
| 许可证 | Apache-2.0 |
| 来源 | ModelScope 上的 ONNX 转换版本 |
| 校验 | 下载时校验 SHA256，首次启动自动获取（约 449 MB） |

该模型不在本仓库内分发，由程序在首次运行时下载到 `backend/embedding/`。

---

## 遵守义务清单

分发本项目时，你必须：

- [ ] 保留 `LICENSE`（AGPL-3.0 全文，逐字不得修改）
- [ ] 保留 `licenses/` 下全部许可证全文
- [ ] 保留上游作者的版权声明
- [ ] 保留 `CHANGES.md` 中对修改的标注
- [ ] 若通过网络提供服务，向使用者提供完整对应源码（AGPL §13）
- [ ] 不得把 `contrib/ai-novel-assistant/` 用于商业分发，除非另行取得授权
