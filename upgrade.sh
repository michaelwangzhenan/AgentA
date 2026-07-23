#!/usr/bin/env bash

# 任一步失败就停，避免半更新状态继续跑。
set -euo pipefail

cd /home/admin/AgentA

PREV_HEAD=$(git rev-parse HEAD)
git pull
echo "Deployed: $(git log -1 --oneline)"

# 本次 pull 变更了哪些文件（Already up to date 时为空）
CHANGED=$(git diff --name-only "$PREV_HEAD" HEAD 2>/dev/null || true)

changed_matches() {
  [ -n "$CHANGED" ] && echo "$CHANGED" | grep -qE "$1"
}

# MCP：git 里可能是 Windows 路径，VPS 需改成 Linux venv
MCP_CONFIG=".agenta/mcp/config.json"
MCP_FIXED=false
if grep -q '\.venv/Scripts/python\.exe' "$MCP_CONFIG"; then
  sed -i 's#\.venv/Scripts/python\.exe#.venv/bin/python#' "$MCP_CONFIG"
  MCP_FIXED=true
fi

NEED_PIP=false
NEED_NPM_INSTALL=false
NEED_NPM_BUILD=false
NEED_BACKEND_RESTART=false
NEED_NGINX_RELOAD=false
WORD_PACK_CHANGED=false

if changed_matches '^requirements\.txt$'; then
  NEED_PIP=true
  NEED_BACKEND_RESTART=true
fi

if changed_matches '^frontend/package\.json$|^frontend/package-lock\.json$'; then
  NEED_NPM_INSTALL=true
  NEED_NPM_BUILD=true
  NEED_NGINX_RELOAD=true
elif changed_matches '^frontend/'; then
  NEED_NPM_BUILD=true
  NEED_NGINX_RELOAD=true
fi

if changed_matches '^(src/|tools/|\.agenta/)'; then
  NEED_BACKEND_RESTART=true
fi

# 敏感词库：运行时只读 deny.tsv 等，须在开发机 build 后随 git 发布；线上不执行 build，变更后重启以重新加载。
if changed_matches '^resources/sensitive_words/(deny\.tsv|allow\.txt|metadata\.json|trad_simp\.tsv)$'; then
  NEED_BACKEND_RESTART=true
  WORD_PACK_CHANGED=true
fi

if [ "$MCP_FIXED" = true ]; then
  NEED_BACKEND_RESTART=true
fi

if [ -z "$CHANGED" ] && [ "$MCP_FIXED" = false ]; then
  echo "Already up to date — skipping build/restart."
else
  if [ "$NEED_PIP" = true ]; then
    echo ">> requirements.txt changed — pip install"
    .venv/bin/pip install -r requirements.txt
  fi

  if [ "$NEED_NPM_BUILD" = true ]; then
    # 低内存 VPS：构建前端前先停后端腾内存
    echo ">> building frontend (stopping backend to free memory)"
    sudo systemctl stop agenta-backend

    cd frontend
    if [ "$NEED_NPM_INSTALL" = true ]; then
      echo ">> frontend deps changed — npm install"
      npm install
    fi
    npm run build
    cd ..

    NEED_BACKEND_RESTART=true
    NEED_NGINX_RELOAD=true
  fi

  if [ "$NEED_BACKEND_RESTART" = true ]; then
    sudo systemctl restart agenta-backend
  fi

  if [ "$NEED_NGINX_RELOAD" = true ]; then
    sudo nginx -t
    sudo systemctl reload nginx
  fi

  if [ "$NEED_BACKEND_RESTART" = true ] || [ "$NEED_NGINX_RELOAD" = true ]; then
    sudo systemctl status agenta-backend nginx --no-pager
  fi
fi

# 等后端就绪（最多 30 秒）
HEALTH_OK=false
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null; then
    curl -s http://127.0.0.1:8000/api/health; echo
    HEALTH_OK=true
    break
  fi
  sleep 1
done

if [ "$HEALTH_OK" != true ]; then
  echo "ERROR: backend health check failed after 30s"
  journalctl -u agenta-backend -n 20 --no-pager
  exit 1
fi

if [ "$WORD_PACK_CHANGED" = true ]; then
  echo ">> sensitive word pack changed — reload verified at startup"
  .venv/bin/python tools/cli/sensitive_word_cli.py status
  if ! journalctl -u agenta-backend --since "2 min ago" --no-pager 2>/dev/null \
    | grep -q 'sensitive_word_filter.*词库加载完成'; then
    echo "WARN: 最近启动日志未见「词库加载完成」，请执行: journalctl -u agenta-backend -n 30 --no-pager"
  fi
fi

# nginx 健康检查：HTTPS 部署后裸访 127.0.0.1 可能 404；reload / 后端重启后需短暂等待。
# 优先本机 Host 头探测，避免走公网 HTTPS 的偶发超时。
check_nginx_once() {
  local code line conf="/etc/nginx/sites-available/agenta"

  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1/" 2>/dev/null || true)
  if [[ "$code" =~ ^[23] ]]; then
    line=$(curl -sI --max-time 5 "http://127.0.0.1/" 2>/dev/null | head -1)
    [ -n "$line" ] && echo "$line"
    return 0
  fi

  [ -f "$conf" ] || return 1

  local name
  while read -r name; do
    [ -z "$name" ] || [ "$name" = "_" ] && continue
    if [[ "$name" =~ ^[0-9.]+$ ]]; then
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "http://${name}/" 2>/dev/null || true)
      if [[ "$code" =~ ^[23] ]]; then
        curl -sI --max-time 5 "http://${name}/" 2>/dev/null | head -1
        return 0
      fi
    else
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Host: ${name}" "http://127.0.0.1/" 2>/dev/null || true)
      if [[ "$code" =~ ^[23] ]]; then
        curl -sI --max-time 5 -H "Host: ${name}" "http://127.0.0.1/" 2>/dev/null | head -1
        return 0
      fi
      code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "https://${name}/" 2>/dev/null || true)
      if [[ "$code" =~ ^[23] ]]; then
        curl -sI --max-time 8 "https://${name}/" 2>/dev/null | head -1
        return 0
      fi
    fi
  done < <(grep -h 'server_name' "$conf" | sed -E 's/.*server_name[[:space:]]+([^;]+);/\1/' | tr ' ' '\n' | sort -u)

  return 1
}

NGINX_OK=false
NGINX_LINE=""
for _ in $(seq 1 30); do
  if NGINX_LINE=$(check_nginx_once); then
    echo "$NGINX_LINE"
    NGINX_OK=true
    break
  fi
  sleep 1
done

if [ "$NGINX_OK" != true ]; then
  echo "ERROR: nginx check failed after 30s"
  sudo nginx -t 2>&1 || true
  exit 1
fi
