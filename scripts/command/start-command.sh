#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
if [ ! -f "$PROJECT_ROOT/frontend/command/dist/index.html" ]; then
  echo "Build first: cd frontend/command && npm ci && npm run build" >&2
  exit 1
fi
mkdir -p "$PROJECT_ROOT/data/command-demo"
if [ -z "${DEMO_DATABASE:-}" ]; then
  SKYNET_SESSION_DIR="$(mktemp -d "$PROJECT_ROOT/data/command-demo/session.XXXXXX")"
  DEMO_DATABASE="$SKYNET_SESSION_DIR/demo.db"
fi
cd "$PROJECT_ROOT"
exec "${SKYNET_PYTHON:-python3}" -B -m src.integration.server --database "$DEMO_DATABASE" --port "${PORT:-8765}" --frontend "$PROJECT_ROOT/frontend/command/dist"
