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

sleep 5

sudo systemctl status agenta-backend nginx --no-pager
curl -s http://127.0.0.1:8000/api/health; echo
curl -I http://127.0.0.1/