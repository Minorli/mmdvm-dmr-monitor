#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
for required in .venv/bin/python native/build/libdmrdecode.so web/dist/index.html; do
    if [[ ! -f "$required" ]]; then
        printf 'Missing %s; follow docs/deployment.md to build the application.\n' "$required" >&2
        exit 1
    fi
done
export PYTHONUNBUFFERED=1
exec "$project_root/.venv/bin/python" -m backend.main
