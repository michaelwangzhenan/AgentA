# 1. Markdown 链接语法

## 1.1. 行内链接

最常用的写法，链接文字和地址写在一起：

```markdown
[显示文字](https://example.com)
```

带鼠标悬浮提示（title）：

```markdown
[显示文字](https://example.com "悬浮提示")
```

## 1.2. 引用式链接

把地址单独定义，正文里用标签引用，适合同一地址多次出现：

```markdown
[显示文字][标签]

[标签]: https://example.com "可选的悬浮提示"
```

标签可省略，省略时直接用显示文字当标签：

```markdown
[显示文字][]

[显示文字]: https://example.com
```

## 1.3. 自动链接

直接把 URL 或邮箱用尖括号括起来，自动变成可点击链接：

```markdown
<https://example.com>
<someone@example.com>
```

## 1.4. 站内 / 文件链接

链接到同仓库的其他文件，用相对路径：

```markdown
[worktree 命令](./worktree.md)
[上级目录的设计文档](../design.md)
```

## 1.5. 锚点链接（章节跳转）

跳到本文档某个标题，地址用 `#` 加标题的锚点（小写、空格转 `-`）：

```markdown
[跳到行内链接](#11-行内链接)
```

跳到其他文件的某个标题：

```markdown
[设计文档的某节](./design.md#某节标题)
```

## 1.6. 图片链接

图片在链接语法前加 `!`：

```markdown
![替代文字](./images/demo.png "悬浮提示")
```

让图片也可点击（图片外再套一层链接）：

```markdown
[![替代文字](./images/demo.png)](https://example.com)
```
