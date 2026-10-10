import asyncio

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.server import create_app


def application(tmp_path, token=""):
    cfg = Settings(tmp_path, "pi-star@radio.local", "https://api.example.test", "", "test", "small", 8788, control_token=token)
    app = create_app(cfg)
    runtime = app.state.runtime
    counts = {"starts":0, "stops":0}

    async def dormant():
        await asyncio.Event().wait()

    def start_pipeline():
        counts["starts"] += 1
        runtime.pipeline.running = True

    async def stop_pipeline():
        counts["stops"] += 1
        runtime.pipeline.running = False

    runtime.capture = dormant
    runtime.pipeline.start = start_pipeline
    runtime.pipeline.stop = stop_pipeline
    return app, counts


def control_headers(token=""):
    return {"X-Starwave-Request":"1", **({"Authorization":"Bearer " + token} if token else {})}


def test_web_starts_stopped_and_operator_controls_are_idempotent(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/status").json()["listening_state"] == "stopped"
        assert calls["starts"] == 0
        assert client.post("/api/listening/start", json={}, headers=control_headers()).status_code == 202
        client.post("/api/listening/start", json={}, headers=control_headers())
        assert calls["starts"] == 1
        assert app.state.runtime.store.get_setting("listening_requested") is True
        response = client.post("/api/listening/stop", json={}, headers=control_headers())
        assert response.json()["listening_state"] == "stopped"
        assert app.state.runtime.store.get_setting("listening_requested") is False
        assert client.get("/api/health").json()["ok"]
        assert client.get("/api/calls").status_code == 200


def test_cross_site_or_missing_csrf_header_cannot_start_capture(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/listening/start", json={}).status_code == 403
        assert client.post("/api/listening/start", json={}, headers={**control_headers(), "Origin":"https://evil.example"}).status_code == 403
        assert client.post("/api/listening/start", json={"host":"bad"}, headers=control_headers()).status_code == 400
        assert calls["starts"] == 0


def test_control_token_is_required_and_never_returned(tmp_path):
    app, calls = application(tmp_path, "private-control")
    with TestClient(app) as client:
        response = client.post("/api/listening/start", json={}, headers=control_headers("wrong"))
        assert response.status_code == 401
        assert "private-control" not in response.text
        assert "wrong" not in response.text
        assert "private-control" not in client.get("/api/status").text
        assert client.post("/api/listening/start", json={}, headers=control_headers("private-control")).status_code == 202
        assert calls["starts"] == 1


def test_saved_operator_choice_survives_restart_and_manual_stop(tmp_path):
    app, _ = application(tmp_path)
    with TestClient(app) as client:
        client.post("/api/listening/start", json={}, headers=control_headers())
    second, calls = application(tmp_path)
    with TestClient(second) as client:
        assert client.get("/api/status").json()["listening_state"] == "running"
        assert calls["starts"] == 1
        client.post("/api/listening/stop", json={}, headers=control_headers())
    third, calls = application(tmp_path)
    with TestClient(third) as client:
        assert client.get("/api/status").json()["listening_state"] == "stopped"
        assert calls["starts"] == 0


def test_read_rate_limit_cannot_prevent_an_authenticated_operator_stop(tmp_path):
    app, calls = application(tmp_path, "private-control")
    headers = control_headers("private-control")
    with TestClient(app) as client:
        assert client.post("/api/listening/start", json={}, headers=headers).status_code == 202
        runtime = app.state.runtime
        assert runtime.pipeline.running
        assert any(group == "capture" for group, _ in runtime.supervisor.tasks.values())
        # Exhaust reads whether the old shared budget counted START or not.
        for _ in range(121):
            response = client.get("/api/health")
        assert response.status_code == 429
        response = client.post("/api/listening/stop", json={}, headers=headers)
        assert response.status_code == 200
        assert response.json()["listening_state"] == "stopped"
        assert runtime.store.get_setting("listening_requested") is False
        assert not runtime.pipeline.running
        assert not any(group == "capture" for group, _ in runtime.supervisor.tasks.values())
        assert calls["stops"] == 1


def test_start_stop_and_read_requests_use_independent_rate_budgets(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        for _ in range(20):
            assert client.post("/api/listening/start", json={}, headers=control_headers()).status_code == 202
        assert client.post("/api/listening/start", json={}, headers=control_headers()).status_code == 429
        for _ in range(30):
            assert client.post("/api/listening/stop", json={}, headers=control_headers()).status_code == 200
        assert client.post("/api/listening/stop", json={}, headers=control_headers()).status_code == 429
        assert client.get("/api/health").status_code == 200
        assert calls == {"starts":1, "stops":1}


def test_invalid_origin_cannot_stop_an_active_listener(tmp_path):
    app, calls = application(tmp_path)
    with TestClient(app) as client:
        client.post("/api/listening/start", json={}, headers=control_headers())
        response = client.post("/api/listening/stop", json={}, headers={**control_headers(), "Origin":"https://evil.example"})
        assert response.status_code == 403
        assert app.state.runtime.store.get_setting("listening_requested") is True
        assert app.state.runtime.listening_state == "running"
        assert app.state.runtime.pipeline.running
        assert calls["stops"] == 0


def test_cancelling_stop_http_handler_does_not_cancel_operator_stop(tmp_path):
    from starlette.requests import Request
    app, _ = application(tmp_path)
    runtime = app.state.runtime

    async def run():
        stopping, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        runtime.listening_state = "running"
        runtime.pipeline.running = True
        runtime.store.set_setting("listening_requested", True)

        async def gated_pipeline_stop():
            stopping.set()
            await release.wait()
            runtime.pipeline.running = False
            finished.set()

        runtime.pipeline.stop = gated_pipeline_stop
        runtime.supervisor.spawn("capture", asyncio.Event().wait(), group="capture")

        async def receive():
            return {"type":"http.request", "body":b"{}", "more_body":False}

        request = Request({"type":"http", "method":"POST", "scheme":"http", "path":"/api/listening/stop", "query_string":b"", "server":("testserver",80), "headers":[(b"x-starwave-request",b"1"),(b"content-type",b"application/json")]}, receive)
        endpoint = next(route.endpoint for route in app.routes if route.path == "/api/listening/stop")
        handler = asyncio.create_task(endpoint(request))
        try:
            await asyncio.wait_for(stopping.wait(), 1)
            assert runtime.store.get_setting("listening_requested") is False
            handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)
            release.set()
            await asyncio.wait_for(finished.wait(), 1)

            async def stopped():
                while runtime.listening_state != "stopped":
                    await asyncio.sleep(.005)

            await asyncio.wait_for(stopped(), 1)
            assert not runtime.pipeline.running
            assert not runtime.supervisor.tasks
        finally:
            release.set()
            handler.cancel()
            await asyncio.gather(handler, return_exceptions=True)
            await runtime.stop()

    asyncio.run(run())
