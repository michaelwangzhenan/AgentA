#!/usr/bin/env bash
set -euo pipefail

cd ~/AgentA

# change mcp config for linux env
MCP_CONFIG=".agenta/mcp/config.json"
if grep -q '\.venv/Scripts/python\.exe' "$MCP_CONFIG"; then
  sed -i 's#\.venv/Scripts/python\.exe#.venv/bin/python#' "$MCP_CONFIG"
fi

cd frontend && npm run build && cd ..
sudo systemctl restart agenta-backend
sudo systemctl restart nginx
sudo systemctl status agenta-backend nginx --no-pager

sleep 5

# 等后端就绪（最多 30 秒）
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8000/api/health >/dev/null; then
    curl -s http://127.0.0.1:8000/api/health; echo
    break
  fi
  sleep 1
done

curl -I http://127.0.0.1/ || echo "WARN: nginx 检查失败"