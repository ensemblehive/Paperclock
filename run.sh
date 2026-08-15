#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c "import sys" 2>/dev/null; then
  echo "Setting up Python virtual environment..."
  rm -rf .venv
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
  .venv/bin/pip install --quiet -e .
fi

if [[ ! -d node_modules ]]; then
  npm install --no-audit --no-fund
fi

.venv/bin/paperclock serve &
engine_pid=$!
trap 'kill "$engine_pid" 2>/dev/null || true' EXIT INT TERM

for _ in {1..40}; do
  if .venv/bin/python -c 'from urllib.request import urlopen; urlopen("http://127.0.0.1:4312/api/health", timeout=.2)' 2>/dev/null; then
    break
  fi
  if ! kill -0 "$engine_pid" 2>/dev/null; then
    echo "Paperclock's local engine could not start." >&2
    exit 1
  fi
  sleep 0.1
done

npm run dev
