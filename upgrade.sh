#!/usr/bin/env bash
set -euo pipefail

cd ~/AgentA


cd frontend && npm run build && cd ..
sudo systemctl restart agenta-backend
sudo systemctl reload nginx

sudo systemctl status agenta-backend nginx --no-pager
curl -s http://127.0.0.1:8000/api/health; echo
curl -I http://127.0.0.1/