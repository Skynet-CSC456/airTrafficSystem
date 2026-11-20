#!/usr/bin/env bash
# Compatibility entry point; implementation belongs to the Command scripts.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")" && pwd)"
exec bash "$PROJECT_ROOT/scripts/command/start-command.sh" "$@"
