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

# nginx 健康检查：HTTPS 部署后 server_name 为域名，裸访 127.0.0.1 会 404
check_nginx() {
  if curl -sfI "http://127.0.0.1/" >/dev/null 2>&1; then
    curl -sI "http://127.0.0.1/" | head -1
    return 0
  fi

  local conf="/etc/nginx/sites-available/agenta"
  [ -f "$conf" ] || return 1

  local name
  while read -r name; do
    [ -z "$name" ] || [ "$name" = "_" ] && continue
    if [[ "$name" =~ ^[0-9.]+$ ]]; then
      if curl -sfI "http://${name}/" >/dev/null 2>&1; then
        curl -sI "http://${name}/" | head -1
        return 0
      fi
    else
      if curl -sfI "https://${name}/" >/dev/null 2>&1; then
        curl -sI "https://${name}/" | head -1
        return 0
      fi
      if curl -sfI -H "Host: ${name}" "http://127.0.0.1/" >/dev/null 2>&1; then
        curl -sI -H "Host: ${name}" "http://127.0.0.1/" | head -1
        return 0
      fi
    fi
  done < <(grep -h 'server_name' "$conf" | sed -E 's/.*server_name[[:space:]]+([^;]+);/\1/' | tr ' ' '\n' | sort -u)

  return 1
}

if ! check_nginx; then
  echo "ERROR: nginx check failed"
  exit 1
fi
