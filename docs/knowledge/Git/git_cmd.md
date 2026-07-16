# 1. Git 常用命

## 1.1. 配置

### 1.1.1. 身份与基本配置

```bash
# 设全局用户名 / 邮箱
git config --global user.name "名字"
git config --global user.email "you@example.com"

# 看某项配置
git config user.name

# 看全部配置（带来源）
git config --list --show-origin

# 设默认分支名为 main
git config --global init.defaultBranch main
```

### 1.1.2. 代理设置

走 HTTP(S) 拉取 GitHub 等仓库时，给 git 单独设代理（端口换成你本机代理的实际端口，如 7890 / 1080）：

```bash
# 设置 http / https 代理
git config --global http.proxy http://127.0.0.1:7890
git config --global https.proxy http://127.0.0.1:7890

# 查看当前代理
git config --global --get http.proxy

# 取消代理（不走代理时务必清掉，否则代理一关 git 就连不上）
git config --global --unset http.proxy
git config --global --unset https.proxy
```

如果只想对某个站点走代理（比如只代理 github.com），可以加 host 限定：

```bash
git config --global http.https://github.com.proxy http://127.0.0.1:7890
```

SOCKS5 代理（如 SSH 协议的仓库或想用 socks）：

```bash
git config --global http.proxy socks5://127.0.0.1:1080
```

注意：git config http.proxy 只对 HTTP(S) 协议的远程生效。如果你的远程是 git@github.com:...（SSH 协议），代理要配在 SSH 那边（~/.ssh/config 里用 ProxyCommand），git 的 http.proxy 不管用。

## 1.2. 拿代码（创建 / 克隆）

```bash
# 当前目录初始化为仓库
git init

# 克隆远程仓库
git clone <url>

# 克隆到指定目录
git clone <url> 目录名

# 只克隆最近 1 次提交（省流量）
git clone --depth 1 <url>
```

## 1.3. 日常开发（改 → 看 → 提交）

```bash
# 看当前状态（改了啥 / 没跟踪啥）
git status

# 状态简洁版
git status -s

# 看未暂存的改动
git diff

# 看已暂存的改动
git diff --staged

# 暂存指定文件
git add 文件

# 暂存全部改动
git add -A

# 提交
git commit -m "说明"

# 暂存 + 提交已跟踪文件（跳过 add）
git commit -am "说明"

# 改上一条提交（未 push 才用）
git commit --amend
```

提交说明用 HEREDOC 写多行（避免引号转义问题）：

```bash
git commit -m "$(cat <<'EOF'
标题：一句话说清这次改了什么

- 要点 1
- 要点 2
EOF
)"
```

## 1.4. 分支

```bash
# 看本地分支（* 是当前）
git branch

# 看所有分支（含远程）
git branch -a

# 看分支跟踪关系
git branch -vv

# 建分支（不切过去）
git branch 新分支

# 建分支并切过去
git checkout -b 新分支
# 或：git switch -c 新分支

# 切分支
git checkout 分支
# 或：git switch 分支

# 删已合并的分支
git branch -d 分支

# 强删未合并的分支
git branch -D 分支

# 重命名当前分支
git branch -m 新名字
```

## 1.5. 同步远程（取 / 拉 / 推）

```bash
# 看远程地址
git remote -v

# 加远程
git remote add origin <url>

# 只取远程更新（不合并）
git fetch

# 取并合并到当前分支
git pull

# 取并用变基方式合（线性历史）
git pull --rebase

# 推送当前分支
git push

# 首次推送并建立跟踪
git push -u origin 分支

# 推送一个新建的本地分支
git push -u origin HEAD
```

fetch 与 pull 的区别：fetch 只把远程的新提交下载下来、不动你的工作区；pull = fetch + 自动合并进当前分支。想先看看再决定怎么合，就先 fetch 再手动 merge。

## 1.6. 合并

```bash
# 把某分支合进当前分支
git merge 分支

# 合并时强制留一个 merge 提交
git merge --no-ff 分支

# 变基到某分支（改写历史，慎用）
git rebase 分支

# 放弃正在进行的合并
git merge --abort

# 放弃正在进行的变基
git rebase --abort
```

### 1.6.1. 解决冲突的流程

```bash
git merge 分支            # 报告 CONFLICT
git status               # 看哪些文件冲突（标记 UU）
# 手动编辑冲突文件，删掉 <<<<<<< ======= >>>>>>> 标记，留下想要的内容
git add 已解决的文件
git commit               # 完成合并（merge 会用默认信息）
```

merge vs rebase 选择：多人协作 / 已 push / 多 worktree 场景用 merge（保留真实历史、好回溯）；只想要本地线性干净历史、且分支没 push 过，才考虑 rebase。

## 1.7. 撤销 / 回退

```bash
# 丢弃某文件的未暂存改动
git checkout -- 文件
# 或：git restore 文件

# 把已暂存的文件退回未暂存
git restore --staged 文件

# 撤销上次提交、保留改动在工作区
git reset --soft HEAD~1

# 撤销上次提交、改动退回未暂存
git reset HEAD~1

# 撤销上次提交、连改动一起丢弃（危险）
git reset --hard HEAD~1

# 用一个新提交反做某次提交（安全，适合已 push）
git revert <commit>
```

reset --hard 会丢工作区改动且不可逆；已经 push 的提交别用 reset 改写，用 revert 生成反向提交更安全。

## 1.8. 查看历史

```bash
# 看提交历史
git log

# 一行一条
git log --oneline

# 带分支图
git log --oneline --graph --all

# 看某文件的改动历史
git log -p 文件

# 看某次提交的内容
git show <commit>

# 看最后一次提交的完整改动
git show
# 或：git show HEAD / git log -1 -p

# 只看最后一次提交改了哪些文件
git show --stat

# 只看最后一次提交里某文件的改动
git show HEAD -- 文件

# 查每行是谁改的
git blame 文件
```

## 1.9. 临时保存（stash）

手头改了一半、需要先切去干别的，又不想提交半成品：

```bash
# 暂存当前改动并清空工作区
git stash

# 暂存时带说明
git stash push -m "说明"

# 看暂存列表
git stash list

# 恢复最近一次（并从栈删除）
git stash pop

# 恢复但保留在栈里
git stash apply

# 丢弃最近一次暂存
git stash drop
```

## 1.10. 收尾 / 清理

```bash
# 看哪些文件会被忽略 / 未跟踪
git status --ignored

# 预览将被清理的未跟踪文件（不真删）
git clean -n

# 删除未跟踪文件（危险）
git clean -fd

# 打标签
git tag v1.0.0

# 推送标签
git push origin v1.0.0
```

git clean -fd 会真删未跟踪文件且不可逆，动手前先 git clean -n 看清楚删哪些。

## 1.11. 典型流程

### 1.11.1. 从当前分支开新分支

```bash
git switch -c feature-x        # 从当前分支开新分支
# ... 改代码 ...
git add -A
git commit -m "feature-x: 实现某功能"
git switch main
git pull                       # 先把 main 更到最新
git merge feature-x            # 合并
git push                       # 推送
git branch -d feature-x        # 删掉用完的分支
```

### 1.11.2. 合并多个 commit 为一个（squash）

把最后几个零碎 commit 攒成一个干净的提交。两种方法都会改写历史，只在没 push（或确认能强推）时用。

方法一：reset --soft（合并最后连续的几个，最简单）

```bash
git reset --soft HEAD~2    # 指针回退 2 个 commit，改动全留在暂存区
git commit -m "合并后的说明"  # 重新提交成一个
```

方法二：git rebase -i（更通用，能合并任意位置、还能 reword / 删 / 调顺序）

```bash
git rebase -i HEAD~2       # 或写基准 commit id：git rebase -i <要保留不动的那个 commit>
```

打开的编辑器里按从旧到新列出 commit（跟 git log 相反），把要并掉的那条 pick 改成 s 或 f：

```
pick fdc5f38 增加 复杂任务行动准则
s    18793ab x
```

| 改成 | 含义 |
|---|---|
| s（squash） | 合并到上一个，保留两条信息让你编辑 |
| f（fixup） | 合并到上一个，丢弃这条信息 |
| r（reword） | 不合并，只改这条提交信息 |
| d（drop） | 删掉这条 commit |

保存退出即完成。HEAD~2 表示往回数 2 个；rebase 会列出基准 commit 之后的所有 commit 供编辑。

已 push 的话需要 git push --force-with-lease（比 --force 安全，会校验远程没被别人改过），协作分支慎用。
