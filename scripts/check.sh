#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--docker" ) ]]; then
    printf 'Usage: %s [--docker]\n' "$0" >&2
    exit 2
fi
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
uv sync --locked
uv run --frozen ruff check backend tests scripts deploy
./scripts/build-codec.sh
uv run --frozen pytest -q
npm --prefix web ci
npm --prefix web test
npm --prefix web run build
bash -n scripts/start.sh scripts/install-service.sh scripts/build-codec.sh scripts/check.sh
sh -n scripts/container-entrypoint.sh
if command -v shellcheck >/dev/null; then
    shellcheck scripts/start.sh scripts/install-service.sh scripts/build-codec.sh \
        scripts/container-entrypoint.sh scripts/check.sh
fi
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    # Deliberately avoid loading the operator's .env during validation.
    docker compose --env-file /dev/null config --quiet
fi
if [[ "${1:-}" == "--docker" ]]; then
    image="${STARWAVE_CONTAINER_IMAGE:-starwave:check}"
    docker build --tag "$image" .
    STARWAVE_CONTAINER_IMAGE="$image" STARWAVE_CONNECTION_IMAGE="$image" \
        uv run --frozen pytest tests/test_container_image.py tests/test_connection_ssh_integration.py -q
fi
