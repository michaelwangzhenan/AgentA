# Cursor 状态库清理

Cursor 在 Windows 上编辑 Markdown 出现输入卡顿、光标乱跳时，若调整编辑器设置无效，可尝试清理状态数据库 `state.vscdb`。该文件存 Agent / Chat 对话历史，长期使用会膨胀到数 GB，导致 IDE 假死。

路径（Windows）：

```
%APPDATA%\Cursor\User\globalStorage\state.vscdb
```

展开即为：

```
C:\Users\<用户名>\AppData\Roaming\Cursor\User\globalStorage\state.vscdb
```

## 清理影响

| 会丢失 | 会保留 |
|--------|--------|
| 所有 Agent / Chat 对话历史 | `settings.json` 编辑器设置 |
| 部分窗口布局、最近打开记录 | 扩展、快捷键、主题 |
| | 项目代码、`.env`、`.cursor` 规则 |

## 方案 A：整库重建

步骤最少，成功率最高。不保留任何聊天历史。

### 1. 完全退出 Cursor

1. 保存所有文件。
2. 菜单 **File → Exit**，或关闭所有 Cursor 窗口。
3. 系统托盘区找 Cursor 图标，右键 **Quit**。
4. 打开任务管理器（`Ctrl+Shift+Esc`），确认没有 `Cursor.exe` 进程。

### 2. 备份（必做）

PowerShell 执行：

```powershell
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$src = "$env:APPDATA\Cursor\User\globalStorage"
$bak = "$env:USERPROFILE\Desktop\cursor-state-backup-$ts"
New-Item -ItemType Directory -Path $bak -Force
Copy-Item "$src\state.vscdb*" -Destination $bak
Write-Host "Backup saved to: $bak"
```

备份目录在桌面，名称形如 `cursor-state-backup-20260713-100000`。

### 3. 删除数据库

```powershell
$src = "$env:APPDATA\Cursor\User\globalStorage"
Remove-Item "$src\state.vscdb" -Force
Remove-Item "$src\state.vscdb-wal" -Force -ErrorAction SilentlyContinue
Remove-Item "$src\state.vscdb-shm" -Force -ErrorAction SilentlyContinue
```

需删除三个文件：

- `state.vscdb` — 主库
- `state.vscdb-wal` — 写前日志
- `state.vscdb-shm` — 共享内存索引

### 4. 重启并验证

重新打开 Cursor，会自动生成新的空 `state.vscdb`（通常几十 MB 以内）。

检查新库大小：

```powershell
[math]::Round((Get-Item "$env:APPDATA\Cursor\User\globalStorage\state.vscdb").Length / 1MB, 2)
```

再打开 `.md` 文件试中文输入，确认卡顿是否缓解。

## 出问题后恢复

清理后若 Cursor 启动异常、设置丢失、或想还原聊天历史，从备份恢复。

**前提**：Cursor 已完全退出（同上第 1 步）。

### 从桌面备份恢复

将 `$bak` 换成实际备份路径（桌面上的 `cursor-state-backup-*` 文件夹）：

```powershell
$src = "$env:APPDATA\Cursor\User\globalStorage"
$bak = "$env:USERPROFILE\Desktop\cursor-state-backup-20260713-100000"   # 改成实际路径

Remove-Item "$src\state.vscdb*" -Force
Copy-Item "$bak\state.vscdb*" -Destination $src
```

重启 Cursor。

### 从自动备份恢复

`globalStorage` 下可能留有较早的 `state.vscdb.backup`（体积远小于主库）。仅作最后手段，会回到很旧的聊天状态：

```powershell
$src = "$env:APPDATA\Cursor\User\globalStorage"

Remove-Item "$src\state.vscdb" -Force -ErrorAction SilentlyContinue
Remove-Item "$src\state.vscdb-wal" -Force -ErrorAction SilentlyContinue
Remove-Item "$src\state.vscdb-shm" -Force -ErrorAction SilentlyContinue
Copy-Item "$src\state.vscdb.backup" -Destination "$src\state.vscdb"
```

## 清理后仍卡

1. Agent 运行时避免同时手改 Agent 正在编辑的 `.md` 文件。
2. `Ctrl+Shift+P` → **Developer: Open Extension Monitor**，排查 Markdown 相关扩展占用的 CPU / 内存。
3. 用 `cursor --disable-extensions` 启动，排除扩展干扰。
4. 建议定期清理：`state.vscdb` 超过 500 MB 或每月一次。
