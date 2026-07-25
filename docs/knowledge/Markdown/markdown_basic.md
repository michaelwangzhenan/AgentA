# 1. Markdown 链接语法

## 1.1. 文件链接
链接到其他文件，用相对路径：
[Pytest](../Pytest/Pytest.md)

## 1.2. 章节跳转
跳到文档某个标题，地址用 # 加该标题自动生成的锚点。
1. 英文转小写（中文、数字不变）。
2. 去掉标点：句号、逗号、括号、引号、斜杠、叹号等一律删除。
3. 空格（含制表符）换成单个连字符'-'。
4. 相邻标点删除后，两侧文字直接拼接（如 3.5 与前后词变成 35，不是 3-5）。
5. 同名标题重复出现时，从第二个起在锚点末尾加 -1、-2……

当前文件链接：
[1.3. 超链接](#13-超链接)

跨文件章节链接：
[Pytest 1.2 ](../Pytest/Pytest.md#12-测试发现命名约定)

小妙招: 在预览里点标题旁的 ID 复制按钮可获得正确写法

## 1.3. 超链接
[百度](https://www.baidu.com)

带鼠标悬浮提示：
[百度](https://www.baidu.com "这个是百度首页")

直接把 URL 或邮箱用尖括号括起来，自动变成可点击链接：
<https://www.baidu.com>


## 1.5. 图片链接
图片在链接语法前加 `!`：
![AgentA](../../../resources/logo/agentA_logo.svg "我的标志")

标准图片语法不能写宽高，改用 HTML：
<img src="../../../resources/logo/agentA_logo.svg" alt="AgentA" width="120" title="我的标志">

# 2. memaid 格式

%%{init: {'theme':'base', 'themeVariables': {'fontSize':'11px'}, 'flowchart': {'padding':4, 'nodeSpacing':25, 'rankSpacing':30, 'diagramPadding':4}}}%%
