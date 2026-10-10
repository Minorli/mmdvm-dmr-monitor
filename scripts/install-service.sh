#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [[ $# -gt 1 || ( $# -eq 1 && "$1" != "--print" ) ]]; then
    printf 'Usage: %s [--print]\n' "$0" >&2
    exit 2
fi

rendered_unit="$(python3 - "$project_root" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
if any(char in str(root) for char in '\r\n'):
    raise SystemExit('Installation path must not contain a newline')

def quote(value):
    value = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    # ExecStart's executable (the first argument) does not expand variables.
    return '"' + value + '"'

template = (root / 'deploy/pi-star-record.service').read_text()
print(template.replace('@PROJECT_ROOT@', str(root).replace('%', '%%'))
      .replace('@START_SCRIPT@', quote(root / 'scripts/start.sh')), end='')
PY
)"
if [[ "${1:-}" == "--print" ]]; then
    printf '%s\n' "$rendered_unit"
    exit 0
fi

for required in .venv/bin/python scripts/start.sh native/build/libdmrdecode.so web/dist/index.html; do
    if [[ ! -f "$project_root/$required" ]]; then
        printf 'Missing %s; complete the documented local build first.\n' "$required" >&2
        exit 1
    fi
done
install -d "$unit_dir"
printf '%s\n' "$rendered_unit" > "$unit_dir/pi-star-record.service"
chmod 0644 "$unit_dir/pi-star-record.service"
systemctl --user daemon-reload
systemctl --user enable pi-star-record.service
printf 'Installed and enabled pi-star-record.service. Start or restart it when ready:\n'
printf '  systemctl --user restart pi-star-record.service\n'
