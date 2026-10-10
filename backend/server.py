import asyncio
import hmac
import json
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.audio import audio_path
from backend.config import Settings, load_settings
from backend.connection import ConnectionConflict
from backend.runtime import Runtime

log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None, start_capture: bool = True) -> FastAPI:
    settings = settings or load_settings()
    runtime = Runtime(settings)

    @asynccontextmanager
    async def lifespan(app):
        if start_capture:
            await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="Starwave", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime = runtime
    requests = defaultdict(deque)

    @app.middleware("http")
    async def api_limits(request: Request, call_next):
        control_action = {("POST", "/api/listening/start"):"start", ("POST", "/api/listening/stop"):"stop",
                          ("PUT", "/api/connection"):"connection_save", ("POST", "/api/connection/test"):"connection_test",
                          ("PUT", "/api/translation-settings"):"translation_save"}.get((request.method, request.url.path))
        if request.url.path.startswith("/api/"):
            now = time.monotonic()
            client = request.client.host if request.client else "local"
            # Reading, starting and stopping each have a separate budget. Audio,
            # refreshes or repeated starts must never consume the stop allowance.
            key = (client, control_action or "read")
            limit = {"start":20, "stop":30, "connection_save":20, "connection_test":10, "translation_save":20}.get(control_action, 120)
            recent = requests[key]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= limit:
                if control_action:
                    log.warning("Operator control action=%s result=429 client=%s", control_action, client)
                return JSONResponse({"error": "请求过于频繁，请稍后再试"}, status_code=429, headers={"Retry-After": "60"})
            recent.append(now)
            if len(requests) > 1000:
                for ip in [ip for ip, values in requests.items() if not values or now - values[-1] > 60]:
                    requests.pop(ip, None)
        response = await call_next(request)
        if control_action:
            log.info("Operator control action=%s result=%s client=%s", control_action, response.status_code, request.client.host if request.client else "local")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.get("/api/health")
    async def health():
        result = runtime.health()
        return JSONResponse(result, status_code=200 if result["ok"] else 503)

    async def require_control(request: Request, allow_body: bool = False, body_limit: int = 8192):
        if request.headers.get("X-Starwave-Request") != "1" or not request.headers.get("content-type", "").lower().startswith("application/json"):
            raise HTTPException(status_code=403, detail="控制请求必须使用同源JSON接口")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != f"{request.url.scheme}://{request.url.netloc}":
            raise HTTPException(status_code=403, detail="不允许跨站控制请求")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(status_code=403, detail="不允许跨站控制请求")
        if settings.control_token:
            scheme, _, supplied = request.headers.get("Authorization", "").partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.encode(), settings.control_token.encode()):
                raise HTTPException(status_code=401, detail="控制凭据不正确", headers={"WWW-Authenticate":"Bearer"})
        try:
            payload = bytearray()
            async for chunk in request.stream():
                payload.extend(chunk)
                if len(payload) > (body_limit if allow_body else 1024):
                    raise HTTPException(status_code=413, detail="控制请求过大")
            body = json.loads(payload)
        except (ValueError, RecursionError):
            raise HTTPException(status_code=400, detail="请求JSON无效")
        if not isinstance(body, dict) or (not allow_body and body != {}):
            raise HTTPException(status_code=400, detail="控制请求不接受额外参数")
        return body

    @app.get("/api/translation-settings")
    async def translation_settings():
        return runtime.translations.public()

    @app.put("/api/translation-settings")
    async def save_translation_settings(request: Request):
        payload = await require_control(request, allow_body=True, body_limit=16384)
        try:
            return await runtime.configure_translation(payload)
        except ConnectionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError:
            raise HTTPException(status_code=400, detail="翻译设置无法读取或保存，请检查服务数据目录")

    @app.get("/api/connection")
    async def connection():
        return runtime.connections.public()

    async def update_connection(request: Request, test: bool):
        payload = await require_control(request, allow_body=True)
        try:
            return await runtime.configure_connection(payload, test=test)
        except ConnectionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except OSError:
            raise HTTPException(status_code=400, detail="连接设置无法读取或保存，请检查服务数据目录和 SSH 客户端")

    @app.put("/api/connection")
    async def save_connection(request: Request):
        return await update_connection(request, test=False)

    @app.post("/api/connection/test")
    async def check_connection(request: Request):
        return await update_connection(request, test=True)

    @app.post("/api/listening/start")
    async def start_listening(request: Request):
        await require_control(request)
        try:
            result = await runtime.start_listening()
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return JSONResponse(result, status_code=202)

    @app.post("/api/listening/stop")
    async def stop_listening(request: Request):
        await require_control(request)
        # A browser reload or a dropped response must not interrupt an accepted stop.
        return await asyncio.shield(runtime.stop_listening())

    @app.get("/api/status")
    async def status():
        return runtime.status()

    @app.get("/api/calls")
    async def calls(limit: Annotated[int, Query(ge=1, le=400)] = 200):
        return runtime.store.list_calls(limit)

    @app.get("/api/audio/{call_id}")
    async def audio(call_id: Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]):
        call = runtime.store.get(call_id)
        if not call or not call.get("audio_url"):
            raise HTTPException(status_code=404, detail="录音不存在")
        try:
            path = audio_path(settings.data_dir / "audio", call)
        except ValueError:
            raise HTTPException(status_code=404, detail="录音不存在")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="录音不存在")
        return FileResponse(path, media_type="audio/wav", filename=path.name, content_disposition_type="inline", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/events")
    async def events(request: Request):
        if len(runtime.listeners) >= 16:
            raise HTTPException(status_code=503, detail="实时连接暂时已满")
        queue = asyncio.Queue(maxsize=256)
        runtime.listeners.add(queue)

        async def stream():
            try:
                yield "event: status\ndata: " + json.dumps(runtime.status(), ensure_ascii=False) + "\n\n"
                while not await request.is_disconnected():
                    try:
                        event, data = await asyncio.wait_for(queue.get(), timeout=15)
                        if event == "resync":
                            break
                        yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                    except TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                runtime.listeners.discard(queue)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    dist = settings.web_dist
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app
