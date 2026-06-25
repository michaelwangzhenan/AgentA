# 1. taste-skill

## 1.1. 这是个什么工具

taste-skill 是一份**给 AI "审美"的前端 skill**——一个 `SKILL.md` 文本文件。它专治 AI 生成的"一眼假、像模板"的界面（作者称 anti-slop，反垃圾）。

适用范围：


| 适合              | 不适合                    |
| --------------- | ---------------------- |
| 落地页、作品集、营销站、重设计 | 后台 dashboard、数据表格、多步表单 |


它做的几件事：

- **先读需求再动手**：生成前先判断页面类型、受众、风格，避免一上来就套默认审美
- **三个旋钮**：`DESIGN_VARIANCE`（规整↔混乱）、`MOTION_INTENSITY`（静态↔动效）、`VISUAL_DENSITY`（留白↔密集），按需求自动调，也可对话里口头调
- **需求→设计系统映射**：该用官方设计系统就用（如企业风 Fluent、政务 GOV.UK、现代 SaaS 用 Tailwind v4 + shadcn），并禁止手搓官方包已有的 CSS
- **反套路清单**：明令禁止紫色渐变、深色 mesh 居中 hero、三等宽卡片、满屏玻璃拟态等 AI 默认套路

## 1.2. 安装步骤

在项目根目录执行：

```powershell
cd C:\DiskD\sourceCode\mygithub\AgentA
npx skills add https://github.com/Leonxlnx/taste-skill --skill "design-taste-frontend"
```

执行后会往项目里放一个 `SKILL.md`，agent 自动读取，无需额外配置。

想一次装全部变体（含极简、brutalist、重设计、image-to-code 等），去掉 `--skill` 即可：

```powershell
npx skills add https://github.com/Leonxlnx/taste-skill
```

## 1.3. 怎么用

装好后**正常提需求即可**：让 agent 做落地页 / 作品集时，它会先输出一句"Design Read"（把需求读成"什么页面 + 给谁 + 什么风格"），再按规则生成。

想调风格**直接在对话里说**，不用改文件，例如"更极简一点""动效再强些"——对应调那三个旋钮。

常用变体（按需换 `--skill` 后的名字）：


| 安装名                          | 用途                     |
| ---------------------------- | ---------------------- |
| `design-taste-frontend`      | 默认，通用前端审美（v2）          |
| `minimalist-ui`              | 极简 / Notion / Linear 风 |
| `industrial-brutalist-ui`    | brutalist 实验风          |
| `redesign-existing-projects` | 审计并改进现有 UI             |
| `image-to-code`              | 图片→分析→实现               |


## 1.4. 注意事项

- **范围限定**：主要管落地页 / 作品集 / 重设计，对后台 dashboard 帮助有限。
- **v2 仍在迭代**：默认 `design-taste-frontend` 是 v2（experimental），规则措辞可能变；依赖旧行为就装 `design-taste-frontend-v1`。
- `**SKILL.md` 可改**：放在项目里、可直接编辑，按自己的设计方向增删规则。
- **技术栈前提**：默认面向 React/Next + Tailwind v4 + Motion；本仓库前端是 React/TS，基本契合。

## 1.5. 参考链接

- 项目仓库：[https://github.com/Leonxlnx/taste-skill](https://github.com/Leonxlnx/taste-skill)
- 官方网站：[https://tasteskill.dev](https://tasteskill.dev)

# 2. ui-craft

## 2.1. 这是个什么工具

ui-craft 是一份**给 AI 补"设计功力"的前端 skill**——它的口号是"要个 dashboard，给你一个能上生产的"。和 taste-skill 偏营销页不同，**它明确支持 dashboard / admin 后台**，是和 AgentA 这种产品型界面最对口的一个。

它做的几件事：

- **先体检再动手（Discovery）**：动手前先扫项目已有的设计决定（CSS 变量、Tailwind 配置、字体、主题），**有设计系统就尊重、不推倒重来**；没有才问几个问题，避免默认套上"蓝色 + Inter"
- **三个旋钮**：`CRAFT_LEVEL`（出活快↔像素级精修）、`MOTION_INTENSITY`（仅 hover↔滚动联动/页面转场）、`VISUAL_DENSITY`（留白↔仪表盘密集）
- **可打分的评审**：用 Nielsen 10 条可用性 + 6 条设计定律 + 多角色走查，给出**带"业务影响"标签**（阻塞转化 / 增加摩擦 / 损害信任 / 小修饰）的评分卡，能直接贴进 issue
- **按意图分项打磨**：排版、配色、动效、空/错状态、响应式、文案等各有专门 pass

## 2.2. 安装步骤

在项目根目录执行（Cursor 用这个）：

```powershell
cd C:\DiskD\sourceCode\mygithub\AgentA
npx skills add educlopez/ui-craft
```

执行后会在 `.cursor/` 下放好对应 skill，agent 自动读取。

针对 dashboard，还有个把旋钮**锁定在后台密集风**的变体，做后台界面时可一并装：

```powershell
npx skills add educlopez/ui-craft --skill "ui-craft-dense-dashboard"
```

## 2.3. 怎么用

装好后**按意图正常说需求**即可——在 Cursor 这类非 Claude Code 的 agent 里，它的各 pass 不是斜杠命令，而是**靠你的话触发**：


| 你想做什么  | 这么说                                  |
| ------ | ------------------------------------ |
| 新建一个界面 | "做一个用量统计页 / 设置页"                     |
| 美化现有页面 | "polish 这个页面" / "把这个 dashboard 打磨一下" |
| 要评审报告  | "audit 这个组件" / "评审这个页面的 UX"          |
| 加动效    | "给这个弹窗加个入场动效"                        |


它会先做 Discovery（读你现有的 shadcn 主题），再按旋钮生成 / 改造，最后过一遍验收清单才算完。

## 2.4. 注意事项

- **最契合 AgentA**：明确覆盖 dashboard/admin，且尊重现有设计系统，适合**渐进美化已有页面**。
- **斜杠命令仅 Claude Code 原生**：Cursor 里靠意图触发，效果一样，只是没有 `/ui-craft:xxx` 那种显式命令。
- **与 taste-skill 取舍**：做后台/产品界面用 ui-craft，做对外落地页才考虑 taste-skill。

## 2.5. 参考链接

- 项目仓库：[https://github.com/educlopez/ui-craft](https://github.com/educlopez/ui-craft)
- 官方文档：[https://skills.smoothui.dev/docs](https://skills.smoothui.dev/docs)

# 3. shadcn-skills

## 3.1. 这是个什么工具

shadcn-skills 是**专为 shadcn/ui 项目**的两个 skill。AgentA 前端正是 shadcn/ui + Tailwind v4，所以它是"守规范、少返工"的直接补强。包含：

- **shadcn-component-discovery**：动手写自定义组件前，**先在 shadcn 生态里找现成的**（官方 + 30 多个社区 registry），给出最匹配的 2-3 个 + 安装命令，避免重复造轮子
- **shadcn-component-review**：写完/改完组件后**按 shadcn 规范审查**——`data-slot`、用 `gap-`* 而非 `space-y-*`、只用语义化 token（`text-muted-foreground` 等而非裸色阶）、`cn()` 合并 className、移动优先与可访问性

## 3.2. 安装步骤

在项目根目录执行：

```powershell
cd C:\DiskD\sourceCode\mygithub\AgentA
npx skills add mattbx/shadcn-skills
```

只装其中一个：

```powershell
npx skills add mattbx/shadcn-skills -s shadcn-component-discovery
npx skills add mattbx/shadcn-skills -s shadcn-component-review
```

**建议搭配官方 shadcn MCP**（让它能实时搜你配置的 registry、给出可直接跑的安装命令）：

```powershell
npx shadcn@latest mcp init
```

## 3.3. 怎么用

两个 skill 都会**自动触发**，无需特意调用：

- 当你让 agent 加表格/表单/弹窗等常见 UI 时，discovery 会先提示"生态里已有 X，要不要直接装"
- 当 agent 写完/改完组件，review 会给一份**带 ✅/⚠️/❌ 的检查表**并问要不要顺手修

也可显式说"搜一下有没有现成的数据表格组件""审查这个组件是否符合 shadcn 规范"。

## 3.4. 注意事项

- **依赖 shadcn/ui**：它按项目的 `components.json` 识别风格（Vega / Nova / Maia / Lyra / Mira），AgentA 正好满足。
- **和 ui-craft 互补**：ui-craft 管"整体设计与美化"，shadcn-skills 管"守住 shadcn 写法、复用现成组件"，两者一起用最稳。
- **配 MCP 更好用**：不装官方 shadcn MCP 也能用，只是退化为 CLI 命令和手动链接。

## 3.5. 参考链接

- 项目仓库：[https://github.com/mattbx/shadcn-skills](https://github.com/mattbx/shadcn-skills)
- shadcn/ui 官网：[https://ui.shadcn.com](https://ui.shadcn.com)

