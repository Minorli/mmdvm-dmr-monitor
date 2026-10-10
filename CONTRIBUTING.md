# Contributing

Use Python 3.12, uv, Node.js 22+ and a C11 compiler for the supported development
environment. The Docker image provides a reproducible runtime.

```sh
uv sync --frozen
./scripts/build-codec.sh
cd web && npm ci && cd ..
./scripts/check.sh
```

For a UI-only preview, run Vite in `web/`. Keep its proxy on the same origin or
use the backend's static build; control endpoints reject cross-site requests.

Keep changes focused and include tests for behavior that can fail: lease
recovery, storage migration, operator controls, native framing, or stream
reconnection. Use temporary data directories, mocked providers and synthetic
audio. Never point tests at a live service or invoke a real `systemctl` command
from an installer test. Mock service management and external calls.

The application owns one DATA_DIR per process. Capture, processing and Web
lifecycles must remain separate: HTTP starts in standby, while the operator's
explicit listening choice survives supervised restarts. Preserve the UUID
playback API and existing dated recording paths when changing storage.

Do not commit model caches, data, screenshots, logs, `.env`, SSH keys or API
tokens. Do not add sample transcripts from real monitoring sessions. Dependency
updates must keep lockfiles consistent and pass the image smoke tests.

Use conventional commit subjects (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
Explain the behavior change and validation in pull requests; include screenshots
for UI changes and migration/recovery notes for storage changes.
