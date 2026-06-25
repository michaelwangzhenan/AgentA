# 1. web-access

## 1.1. 这是个什么工具

一个给 AI agent 补"完整联网能力"的 skill（`SKILL.md`，MIT）。agent 原生的 WebSearch / WebFetch 缺调度策略、也开不了动态页和登录态，web-access 补的是：

- **联网工具自动选择**：WebSearch / WebFetch / curl / Jina / CDP 按场景自选、可组合
- **直连日常 Chrome（CDP）**：天然带登录态，能开动态页、点击、滚动、上传、视频截帧——小红书 / 微信公众号等反爬站也能读
- **并行子 agent 分治**：多目标分发子 agent 并行，共享一个 proxy、tab 级隔离
- **站点经验积累**：按域名存操作经验，跨 session 复用

> 兼容所有支持 SKILL.md 的 agent（Cursor / Claude Code / Gemini CLI / Codex CLI 等）。

## 1.2. 安装步骤

```powershell
npx skills add eze-is/web-access
```

需要 Node.js 22+ 和 Google Chrome。

## 1.3. 怎么用

装好后正常让 agent 搜索 / 浏览 / 操作网页即可自动触发。它起一个 CDP Proxy（默认端口 3456），agent 用 curl 调 `/new`、`/click`、`/eval`、`/screenshot` 等接口操作浏览器。

## 1.4. 注意事项

- **安全考量**：要开 Chrome 远程调试（CDP），等于把浏览器控制权交给 agent，带着你所有登录态——公司 / 敏感环境要谨慎。
- **适用场景**：调研需要登录页、内部系统、动态 / 反爬站时才有明显价值。

## 1.5. 参考链接

- 项目仓库：[https://github.com/eze-is/web-access](https://github.com/eze-is/web-access)
- 官方网站：[https://web-access.eze.is](https://web-access.eze.is)

# 2. 文档 skills（docx / pdf / pptx / xlsx）

## 2.1. 这是个什么工具

Anthropic 官方的 4 个文档 skill，驱动 Claude "生成文件"能力。让 agent **创建 / 编辑 / 读取** Office 文档：


| skill | 能力                     |
| ----- | ---------------------- |
| docx  | 创建 / 读取 / 编辑 Word，专业排版 |
| pdf   | 抽取文字/表格、合并/拆分、填表单、加水印  |
| pptx  | 创建 / 编辑 PPT，含版式与设计建议   |
| xlsx  | 建表、公式、财务模型、数据清洗        |


实现上：**读取用 markitdown**（前面聊过的库，侧面印证它是事实标准），创建用 pptxgenjs / openpyxl / docx XML。

## 2.2. 安装步骤

Claude Code 走 plugin marketplace：

```
/plugin marketplace add anthropics/skills
/plugin install document-skills@anthropic-agent-skills
```

也可用 `npx skills add anthropics/skills --skill pdf`（按需换 docx/pptx/xlsx）。

## 2.3. 怎么用

装好后提需求时点名即可，如"用 PDF skill 抽取这份表单字段""做一个项目介绍 PPT"。

## 2.4. 注意事项

- **许可证是 source-available（专有），不是开源**：只能参考 / 自用，**不能直接打包进 AgentA 产品**。
- **适用场景**：想让 agent 产出 Word / PPT / Excel（如 backlog 里的"项目介绍 PPT"）时才用。

## 2.5. 参考链接

- 项目仓库：[https://github.com/anthropics/skills/tree/main/skills](https://github.com/anthropics/skills/tree/main/skills)
- 文档说明：[https://anthropics-skills.mintlify.app/skills/document-skills](https://anthropics-skills.mintlify.app/skills/document-skills)

