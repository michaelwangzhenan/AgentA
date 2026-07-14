
# 基础命令

```bash
sudo command           # 以 root 权限执行（改系统配置、启停服务都要）

mkdir -p logs/tmp      # 建目录（-p 可多级）
cat .env               # 整文件输出到终端（适合短文件）
less logs/uvicorn.log  # 分页查看（空格翻页，q 退出）
head -n 20 file        # 看前 20 行
tail -n 80 file        # 看后 80 行
tail -f file           # 持续跟踪新写入（看日志常用）
nano .env              # 用 nano 编辑（Ctrl+O 保存，Ctrl+X 退出）

find . -name "*.log"                # 从当前目录起，按文件名匹配
grep -r "ERROR" logs/               # -r：在子目录里递归搜文本
grep -n "health" logs/uvicorn.log   # -n：输出带行号

chmod 600 ~/.ssh/config   # 6=rw-：仅本人可读写（SSH 要求私钥/config 不能太松）
chmod 700 ~/.ssh          # 7=rwx：仅本人可进目录
chmod o+x /home/admin     # o=其他人，+x=加执行位；目录需 x 才能被「穿过」访问子路径

chown -R www-data:www-data /var/www/html   # -R 递归改属主/属组（nginx 进程用户常是 www-data）

sudo apt update                              # 从软件源拉最新包列表（安装/升级前先跑）
sudo apt upgrade -y                          # 升级已安装包；-y 跳过确认
sudo apt install -y nginx git curl           # 安装指定包
sudo apt remove -y package-name              # 卸载包（配置文件可能仍保留）
apt search nginx                             # 按关键字搜可用包名
dpkg -l | grep nginx                         # dpkg -l 列已装包，管道交给 grep 过滤

pip install -r requirements.txt   # -r：按文件列表批量安装依赖
python -c "import fastapi; print('ok')"   # -c：执行一行 Python，用来验依赖是否装好
```

# 后端

```bash
sudo systemctl status agenta-backend --no-pager   # 看是否在跑、最近几条日志；--no-pager 直接输出不分页
sudo systemctl status nginx --no-pager

sudo systemctl start agenta-backend      # 启动（维护后、首次部署后）
sudo systemctl stop agenta-backend       # 停止（改数据、清库前）
sudo systemctl restart agenta-backend    # 先停再起；改代码、改 .env 后用这个
sudo systemctl reload nginx              # 重新读配置、不断现有连接（nginx 改配置后用）

sudo systemctl enable agenta-backend     # 写入开机自启
sudo systemctl disable agenta-backend    # 取消开机自启

sudo systemctl is-active agenta-backend nginx   # 只答 active/inactive，适合脚本判断
sudo systemctl daemon-reload             # 修改 /etc/systemd/system/*.service 后必须执行


# 进程崩溃、启动失败时看 systemd 日志：
journalctl -u agenta-backend -n 100 --no-pager   # -u：指定单元； -n 100：最近 100 行
journalctl -u agenta-backend -f                  # -f：跟 tail -f 一样实时追加
journalctl -u agenta-backend --since today       # 只看今天的日志

```

# nginx

配置文件一般在 /etc/nginx/sites-available/agenta，软链到 sites-enabled。

```bash
sudo nginx -t                    # 只测配置文件语法，不真正 reload
sudo systemctl reload nginx      # 语法通过后，让 nginx 加载新配置
sudo systemctl restart nginx     # 整个 nginx 进程重启（reload 不够时再用）
tail -f /var/log/nginx/access.log    # 谁访问了哪个 URL、返回码
tail -f /var/log/nginx/error.log     # 500、权限拒绝、反代失败等
```

# curl 接口探测

后端健康检查：
```bash
curl -s http://127.0.0.1:8000/api/health; echo
# -s：不显示下载进度条
# 末尾 echo：补一个换行，JSON 不会和下一行提示符粘在一起
```

前端与 nginx 是否通：
```bash
curl -I http://127.0.0.1/
# -I：等价于 HEAD 方法，只看状态码和响应头，速度快
```


# 系统资源

```bash
free -h                    # -h：人类可读单位；看 Mem 与 Swap 已用/可用
df -h /                    # 根分区磁盘占用；快满时要清日志或备份
nproc                      # 逻辑 CPU 核数
uptime                     # 开机多久、1/5/15 分钟平均负载
top                        # 实时看 CPU/内存占用最高的进程（q 退出）
ps aux --sort=-%mem | head -20   # aux 列全进程；--sort=-%mem 按内存降序；head 只取前 20
```

配置 2G swap（小内存 VPS 建议，防 OOM 杀 agenta-backend）：

```bash
sudo fallocate -l 2G /swapfile          # 在磁盘上预分配 2G 文件（失败可改用 dd）
sudo chmod 600 /swapfile                # 仅 root 可读写
sudo mkswap /swapfile                   # 格式化为 swap 分区
sudo swapon /swapfile                   # 立即启用
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # 写入 fstab，重启后自动挂载
echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-swappiness.conf   # 降低主动换页倾向
sudo sysctl -p /etc/sysctl.d/99-swappiness.conf   # 立刻应用内核参数
free -h                                 # 确认 Swap 行 total 约 2.0Gi
```

# 定时任务（crontab）

```bash
crontab -e                 # 用默认编辑器打开当前用户的定时任务表
crontab -l                 # 列出已配置的定时任务（确认是否写对）
```

AgentA 每日凌晨 3 点备份示例（路径按实际用户改：试用机 /root/AgentA，正式机 /home/admin/AgentA）：

```bash
# 分 时 日 月 周 | 命令（>> 追加日志，2>&1 错误也写入）
0 3 * * * cd /root/AgentA && /root/AgentA/.venv/bin/python tools/cli/backup_cli.py backup --out /root/AgentA/backups --exclude K >> /root/AgentA/logs/backup.log 2>&1
```

维护停服时，crontab 仍会跑；要暂停备份用 crontab -e 注释对应行。

# 其他实用命令

```bash
history | grep nginx       # 在历史命令里搜
man systemctl              # 查手册（q 退出）
which python3.11           # 命令实际路径
env | grep UVICORN         # 看环境变量
date                       # 系统时间（对 crontab、日志时间戳）
reboot                     # 重启整机（维护窗口再用）
```

