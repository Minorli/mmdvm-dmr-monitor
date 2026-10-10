"""Single-owner entrypoint; supervisors turn background failures into restarts."""

import asyncio
import logging

import uvicorn

from backend.config import load_settings
from backend.locking import DataDirectoryLock
from backend.server import create_app


async def serve(app, settings) -> int:
    server = uvicorn.Server(uvicorn.Config(app, host=settings.bind_host, port=settings.port, access_log=False, timeout_graceful_shutdown=5))
    serving = asyncio.create_task(server.serve())
    fatal = asyncio.create_task(app.state.runtime.supervisor.fatal.wait())
    try:
        done, _ = await asyncio.wait((serving, fatal), return_when=asyncio.FIRST_COMPLETED)
        if fatal in done and fatal.result():
            logging.getLogger(__name__).error("Background supervision failed; requesting process restart")
            server.should_exit = True
            await serving
            return 1
        await serving
        return 0
    finally:
        fatal.cancel()
        await asyncio.gather(fatal, return_exceptions=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = load_settings()
    try:
        with DataDirectoryLock(settings.data_dir):
            code = asyncio.run(serve(create_app(settings), settings))
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


if __name__ == "__main__":
    main()
