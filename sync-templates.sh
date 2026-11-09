#!/usr/bin/env bash
# Совместимая обёртка. Реализация синхронизации находится в Python.
set -euo pipefail
cd "$(dirname "$0")"
python_bin="${PYTHON:-python3}"
command -v "$python_bin" >/dev/null 2>&1 || python_bin=python
case "${1:-}" in
  --check) exec "$python_bin" tools/distribution.py check ;;
  "") exec "$python_bin" tools/distribution.py sync ;;
  *) echo "Использование: sync-templates.sh [--check]" >&2; exit 2 ;;
esac
