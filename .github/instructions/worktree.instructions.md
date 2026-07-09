---
applyTo: "**"
---

# worktree 开发注意事项

判断当前是不是在 worktree（额外工作目录），如果是，则按下面规则执行：

- **复用主目录的虚拟环境**：不要重装环境，直接激活主目录那个 `AgentA\.venv\Scripts\Activate.ps1`。例外：当前分支要改依赖才在 worktree 里单独建 venv。
- **`.env` 要自己拷**：测试跑 LLM 前先从主目录拷过来。
