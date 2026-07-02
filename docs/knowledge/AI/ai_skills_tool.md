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

# 3. Humanizer（去 AI 味）

## 3.1. 这是个什么工具

一组「去 AI 味」的 skill（`SKILL.md`，开源），把 AI 写出来的文字改得更像人写的。同一思路有中英两条线：

- **中文版**：`humanizer-zh-plus`，专治中文里的 AI 痕迹——翻译腔、空泛大词、机械排比、口号式收尾，以及中文引号 / 破折号 / 日期等标点排版。共 38 个模式（含 5 个中文特有），并带**误判保护**：单个破折号、正式词汇、干净语法不单独判成 AI 味。
- **英文版**：`blader/humanizer`，上述中文版的上游原版，针对英文散文去 AI 味。

判据来源是维基百科的 *Signs of AI writing*（AI 写作特征）指南。典型用法是**文档 / 博客 / README / 文案写完后过最后一遍**。

> 中英两版**各管各的语言**：英文文本用中文版没意义，反之亦然。

## 3.2. 安装步骤

`npx skills add` 原生支持 Cursor，直接指定 `-a cursor` 即可，无需手动搬目录：

```powershell
# 中文版（个人全局，落到 ~/.cursor/skills/）
npx skills add RobinZorro86/humanizer-zh-plus -a cursor -g -y

# 英文版（写英文内容时再装）
npx skills add blader/humanizer -a cursor -g -y
```

只给本仓库用就去掉 `-g`（落到 `.agents/skills/`）。需要 Node.js。

> 同类只装一个，别叠多个去 AI 味 skill，否则 agent 选择会混乱。

## 3.3. 怎么用

装好后提需求时点名即可，如"用 humanizer 把这段改得更像人写的，减少翻译腔和空泛大词"，也可直接处理文件："请人性化 xxx.md 里的内容"。

## 3.4. 注意事项

- **不是反检测工具**：目标是读起来更自然，不保证绕过 AI 检测器，别这么用。
- **适合成段散文**：博客 / cover letter / 个人简介这类整段文字效果好；简历的逐条 bullet（动词 + 量化 + 结果）不是它的菜，硬用反而改啰嗦。
- **会改写原文**：以"先改写再核对信息有没有丢"的方式用，重要文本改完自己复核一遍。

## 3.5. 参考链接

- 中文版（推荐）：[https://github.com/RobinZorro86/humanizer-zh-plus](https://github.com/RobinZorro86/humanizer-zh-plus)
- 英文原版：[https://github.com/blader/humanizer](https://github.com/blader/humanizer)
- 其他中文版：[op7418/Humanizer-zh](https://github.com/op7418/humanizer-zh)、[idao-cube/humanizer-zh](https://github.com/idao-cube/humanizer-zh)

