#!/usr/bin/env bash
set -euo pipefail

cd ~/AgentA
git pull
cd frontend && npm run build && cd ..
sudo systemctl restart agenta-backend
sudo systemctl reload nginx
