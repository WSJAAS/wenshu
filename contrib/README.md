# contrib/ — 第三方参考件

本目录存放**仅供本地研读**的第三方项目，**不并入本工程本体**。

---

## ai-novel-assistant/

| 项 | 内容 |
| :--- | :--- |
| 来源 | AI-Novel-Assistant |
| 许可证 | **AGPL-3.0-only ＋商业双许可** |
| 状态 | **原样保留，未作任何修改** |
| 体积 | 约 67 MB（已剔除 `.git`、`node_modules`、构建产物） |

### 为什么单独放这里

该项目的 `LICENSE` 写明：

> **Default community license**: GNU Affero General Public License v3.0 only (AGPL-3.0-only).
>
> **Commercial authorization**: Service-style commercial use requires separate
> commercial authorization from the project maintainer. This includes using this
> project, or a modified version of it, as a backend or core service to provide
> SaaS, hosted, managed, or similar services to third parties.

也就是说，它比单纯的开源协议多了一层限制：**把服务型商业用途单独摘出来**，
需要另行取得原作者授权。

### 本仓库如何使用它

**仅作本地技术研读参考。** 具体地：

- 不修改其任何文件；
- 不把它接入本工程的运行链路；
- 不基于它对外提供服务；
- 不分发它的衍生版本。

### 如果你想商用

必须自行联系原作者取得商业授权。
AGPL-3.0 的社区许可不足以覆盖「服务型商业用途」这一项。

### 它有什么参考价值

该项目是 Electron 桌面形态，与本工程的 Web 形态不同，
但在以下方面可作对照：

- 长篇上下文的一致性维护思路
- 桌面端与本地存储的交互设计
- 创作流程的状态机组织方式

---

## 纳入新参考件的约定

若要向本目录加入其它第三方项目，请：

1. 在 `licenses/` 下放入其许可证全文；
2. 在本文件追加一节，写明来源、许可证、修改状态；
3. 若其许可证含商业限制，明确标注「仅本地研读」；
4. 确认其许可证与本工程 AGPL-3.0 主体**不冲突**。

⚠️ 特别注意：**GPL 系与 AGPL 系以外的许可证**（如含商业限制的双许可）
不得并入本体目录，只能放本目录且保持原样。
