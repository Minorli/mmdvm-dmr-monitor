import base64
import json
import stat
import struct

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.connection import validate_details
from backend.connection_probe import parse_keys
from backend.server import create_app

OLD_KEY = "synthetic-initial-key"
NEW_KEY = "synthetic-replacement-key"


def application(tmp_path, monkeypatch):
    def unexpected_network_client(*args, **kwargs):
        raise AssertionError("Saving settings must never make a provider request")
    monkeypatch.setattr("backend.ai.httpx.AsyncClient", unexpected_network_client)
    cfg = Settings(tmp_path, "", "https://initial.example.invalid", OLD_KEY, "initial-model", "small", 8788, control_token="operator-control")
    return create_app(cfg, start_capture=False)


def headers(token="operator-control", **extra):
    return {"X-Starwave-Request":"1", "Authorization":"Bearer " + token, **extra}


def payload(**changes):
    return {"base_url":"https://provider.example.invalid/v1", "model":"test-model", "api_mode":"responses", "enabled":True, "api_key":NEW_KEY, **changes}


def test_authentication_csrf_and_body_size_precede_secret_access(tmp_path, monkeypatch):
    app = application(tmp_path, monkeypatch)
    def forbidden_draft(*args):
        raise AssertionError("Rejected requests must not read or replace a stored key")
    monkeypatch.setattr(app.state.runtime.translations, "draft", forbidden_draft)
    with TestClient(app) as client:
        denied = client.put("/api/translation-settings", json=payload(), headers=headers("wrong"))
        assert denied.status_code == 401
        csrf = client.put("/api/translation-settings", json=payload(), headers={"Authorization":"Bearer operator-control"})
        assert csrf.status_code == 403
        origin = client.put("/api/translation-settings", json=payload(), headers=headers(Origin="https://other.example"))
        assert origin.status_code == 403
        oversized = client.put("/api/translation-settings", json=payload(api_key="x"*17000), headers=headers())
        assert oversized.status_code == 413
        for response in (denied, csrf, origin, oversized):
            assert NEW_KEY not in response.text
            assert OLD_KEY not in response.text


def test_active_listening_rejects_provider_edits_without_reading_candidate_key(tmp_path, monkeypatch):
    app = application(tmp_path, monkeypatch)
    def forbidden_draft(*args):
        raise AssertionError("Running sessions must not change provider credentials")
    monkeypatch.setattr(app.state.runtime.translations, "draft", forbidden_draft)
    with TestClient(app) as client:
        app.state.runtime.listening_state = "running"
        response = client.put("/api/translation-settings", json=payload(), headers=headers())
        assert response.status_code == 409
        assert NEW_KEY not in response.text
        assert not app.state.runtime.translations.path.exists()


@pytest.mark.parametrize("changes", [
    {"enabled":"false"}, {"enabled":{}}, {"api_mode":[]}, {"model":None},
    {"base_url":"https://user:synthetic-secret@example.invalid/v1"},
    {"base_url":"https://example.invalid/v1?key=synthetic-secret"},
    {"api_key":"synthetic-secret\nsecond-line"}, {"api_key":"非ASCII密钥"},
])
def test_invalid_input_is_a_safe_400_and_does_not_persist_or_call_provider(tmp_path, monkeypatch, changes):
    app = application(tmp_path, monkeypatch)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put("/api/translation-settings", json=payload(**changes), headers=headers())
        assert response.status_code == 400
        assert "synthetic-secret" not in response.text
        assert NEW_KEY not in response.text
        assert not app.state.runtime.translations.path.exists()


def test_saved_key_is_write_only_private_and_immediately_applied_to_paused_pipeline(tmp_path, monkeypatch):
    app = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.put("/api/translation-settings", json=payload(), headers=headers())
        assert response.status_code == 200 and response.json()["ok"]
        public = client.get("/api/translation-settings")
        assert public.json()["api_key_configured"]
        for value in (response.text, public.text, client.get("/api/status").text):
            assert NEW_KEY not in value and OLD_KEY not in value
        assert "api_key" not in public.json()
        runtime = app.state.runtime
        manager = runtime.translations
        assert NEW_KEY not in manager.path.read_text()
        document = json.loads(manager.path.read_text())
        keyfile = manager.private_root / document["generation"] / "api_key"
        assert stat.S_IMODE(manager.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
        assert stat.S_IMODE(keyfile.parent.stat().st_mode) == 0o700
        assert runtime.pipeline.translator.key == NEW_KEY
        assert runtime.pipeline.translator.url == "https://provider.example.invalid/v1/responses"
        assert runtime.pipeline.translation_enabled
        assert not runtime.pipeline.running
        assert not runtime.supervisor.tasks


@pytest.mark.parametrize("new_url", ["https://elsewhere.example.invalid/v1", "http://initial.example.invalid/v1", "https://initial.example.invalid/customer/v1"])
def test_changed_provider_cannot_reuse_an_environment_key(tmp_path, monkeypatch, new_url):
    app = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.put("/api/translation-settings", json=payload(base_url=new_url, api_key=""), headers=headers())
        assert response.status_code == 400
        assert OLD_KEY not in response.text
        assert app.state.runtime.pipeline.translator.key == OLD_KEY
        assert not app.state.runtime.translations.path.exists()


def test_disabled_new_provider_and_explicit_clear_never_fall_back_to_old_key(tmp_path, monkeypatch):
    app = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.put("/api/translation-settings", json=payload(api_key="", enabled=False), headers=headers())
        assert response.status_code == 200
        runtime = app.state.runtime
        assert runtime.settings.api_key == ""
        assert runtime.pipeline.translator.key == ""
        assert not runtime.pipeline.translation_enabled
        assert not client.get("/api/translation-settings").json()["api_key_configured"]
        assert client.put("/api/translation-settings", json=payload(), headers=headers()).status_code == 200
        assert client.put("/api/translation-settings", json=payload(api_key="", clear_api_key=True), headers=headers()).status_code == 400
        assert client.put("/api/translation-settings", json=payload(api_key="", enabled=False, clear_api_key=True), headers=headers()).status_code == 200
        assert runtime.settings.api_key == ""


def test_missing_saved_key_does_not_fall_back_to_environment_and_can_be_replaced(tmp_path, monkeypatch):
    app = application(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert client.put("/api/translation-settings", json=payload(), headers=headers()).status_code == 200
        manager = app.state.runtime.translations
        document = json.loads(manager.path.read_text())
        (manager.private_root / document["generation"] / "api_key").unlink()
        public = client.get("/api/translation-settings")
        assert public.status_code == 200
        assert not public.json()["api_key_configured"]
        assert manager.settings().api_key == ""
        assert client.put("/api/translation-settings", json=payload(api_key="another-synthetic-key"), headers=headers()).status_code == 200
        assert app.state.runtime.pipeline.translator.key == "another-synthetic-key"


def test_radio_and_provider_overlays_survive_alternating_saves(tmp_path, monkeypatch):
    app = application(tmp_path, monkeypatch)
    algorithm = b"ssh-ed25519"
    blob = struct.pack(">I", len(algorithm)) + algorithm + struct.pack(">I", 32) + bytes([1])*32
    key = parse_keys("radio.test ssh-ed25519 " + base64.b64encode(blob).decode())[0]
    async def prepared(manager, candidate):
        return validate_details(candidate), key, None
    monkeypatch.setattr("backend.runtime.prepare_connection", prepared)
    with TestClient(app) as client:
        assert client.put("/api/translation-settings", json=payload(), headers=headers()).status_code == 200
        radio = {"host":"radio.test", "port":2222, "username":"pi-star", "auth_mode":"password", "password":"synthetic-radio-password"}
        assert client.put("/api/connection", json=radio, headers=headers()).status_code == 200
        runtime = app.state.runtime
        assert runtime.settings.api_key == NEW_KEY
        assert runtime.settings.api_url == "https://provider.example.invalid/v1"
        password_file = runtime.settings.ssh_password_file
        updated = payload(base_url="https://PROVIDER.example.invalid:443/", api_mode="chat_completions", model="another-model", api_key="")
        assert client.put("/api/translation-settings", json=updated, headers=headers()).status_code == 200
        assert runtime.settings.pi_host == "pi-star@radio.test"
        assert runtime.settings.ssh_port == 2222
        assert runtime.settings.ssh_password_file == password_file
        assert runtime.pipeline.translator.key == NEW_KEY
        assert runtime.pipeline.translator.url == "https://provider.example.invalid/v1/chat/completions"
        assert not runtime.pipeline.running
