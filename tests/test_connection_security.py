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


def application(tmp_path):
    settings = Settings(tmp_path, "", "https://api.example.test", "", "model", "small", 8788, control_token="operator-control")
    return create_app(settings, start_capture=False)


def headers(token="operator-control", **extra):
    return {"X-Starwave-Request":"1", "Authorization":"Bearer " + token, **extra}


def payload(**changes):
    return {"host":"radio.test", "port":22, "username":"pi-star", "auth_mode":"password", "password":"dummy-private-password", **changes}


def key_line(value=1):
    algorithm = b"ssh-ed25519"
    blob = struct.pack(">I", len(algorithm)) + algorithm + struct.pack(">I", 32) + bytes([value])*32
    return "radio.test ssh-ed25519 " + base64.b64encode(blob).decode() + "\n"


@pytest.mark.parametrize("method,path", [("PUT","/api/connection"), ("POST","/api/connection/test")])
def test_authentication_and_csrf_are_checked_before_any_connection_probe(tmp_path, monkeypatch, method, path):
    app = application(tmp_path)
    async def unexpected_probe(*args):
        raise AssertionError("Unauthorized requests must not scan or read credentials")
    monkeypatch.setattr("backend.runtime.prepare_connection", unexpected_probe)
    with TestClient(app) as client:
        wrong_token = client.request(method, path, json=payload(), headers=headers("wrong"))
        assert wrong_token.status_code == 401
        missing_header = client.request(method, path, json=payload(), headers={"Authorization":"Bearer operator-control"})
        assert missing_header.status_code == 403
        cross_origin = client.request(method, path, json=payload(), headers=headers(Origin="https://other.example"))
        assert cross_origin.status_code == 403
        oversized = client.request(method, path, json=payload(password="x"*9000), headers=headers())
        assert oversized.status_code == 413
        for response in (wrong_token, missing_header, cross_origin):
            assert "dummy-private-password" not in response.text
            assert "operator-control" not in response.text


@pytest.mark.parametrize("mode", [[], {}, None, 42])
def test_invalid_authentication_mode_is_a_safe_400_without_network_io(tmp_path, monkeypatch, mode):
    app = application(tmp_path)
    async def unexpected_scan(*args):
        raise AssertionError("Invalid input must not launch SSH tools")
    monkeypatch.setattr("backend.connection_probe.bounded_output", unexpected_scan)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.put("/api/connection", json=payload(auth_mode=mode), headers=headers())
        assert response.status_code == 400
        assert "dummy-private-password" not in response.text


@pytest.mark.parametrize("host", ["fe80::1%eth0", "fe80::1%eth0\nother-host", "fe80::1%eth0,other.test"])
def test_scoped_ipv6_cannot_bypass_host_validation_or_start_network_tools(tmp_path, monkeypatch, host):
    app = application(tmp_path)
    async def unexpected_scan(*args):
        raise AssertionError("Scoped/control-character targets must be rejected before scanning")
    monkeypatch.setattr("backend.connection_probe.bounded_output", unexpected_scan)
    with TestClient(app) as client:
        response = client.put("/api/connection", json=payload(host=host), headers=headers())
        assert response.status_code == 400
        assert "dummy-private-password" not in response.text
        assert not app.state.runtime.connections.path.exists()


def test_connection_editing_and_testing_are_rejected_while_running(tmp_path, monkeypatch):
    app = application(tmp_path)
    async def unexpected_probe(*args):
        raise AssertionError("Active listening must not race a connection change")
    monkeypatch.setattr("backend.runtime.prepare_connection", unexpected_probe)
    with TestClient(app) as client:
        app.state.runtime.listening_state = "running"
        assert client.put("/api/connection", json=payload(), headers=headers()).status_code == 409
        assert client.post("/api/connection/test", json=payload(), headers=headers()).status_code == 409


def test_public_settings_and_json_never_contain_saved_password(tmp_path):
    app = application(tmp_path)
    manager = app.state.runtime.connections
    secret = "  dummy-private-password  "
    manager.save(validate_details(payload(password=secret)), key_line())
    with TestClient(app) as client:
        response = client.get("/api/connection")
        assert response.status_code == 200
        assert response.json()["password_configured"]
        assert "password" not in response.json()
        assert secret not in response.text
        assert "dummy-private-password" not in manager.path.read_text()
        document = json.loads(manager.path.read_text())
        directory = manager.private_root / document["generation"]
        assert stat.S_IMODE(manager.path.stat().st_mode) == 0o600
        assert stat.S_IMODE((directory / "password").stat().st_mode) == 0o600
        assert manager.draft(payload(password="")).password == secret


@pytest.mark.parametrize("changes", [{"host":"another.test"}, {"port":2222}, {"username":"another-user"}])
def test_empty_password_never_reuses_a_secret_for_a_changed_identity(tmp_path, changes):
    app = application(tmp_path)
    manager = app.state.runtime.connections
    manager.save(validate_details(payload()), key_line())
    with pytest.raises(ValueError, match="重新输入"):
        manager.draft(payload(password="", **changes))
    app.state.runtime.store.close()


def test_untrusted_and_changed_fingerprints_never_attempt_password_authentication(tmp_path, monkeypatch):
    app = application(tmp_path)
    scans, probes = [], []
    async def scan(*command):
        scans.append(command)
        return key_line(len(scans))
    async def unexpected_authentication(*args):
        probes.append(args)
        raise AssertionError("A changed host key must not receive the password")
    monkeypatch.setattr("backend.connection_probe.bounded_output", scan)
    monkeypatch.setattr("backend.runtime.test_connection", unexpected_authentication)
    with TestClient(app) as client:
        first = client.post("/api/connection/test", json=payload(), headers=headers())
        assert first.status_code == 200
        assert first.json()["needs_trust"]
        assert "dummy-private-password" not in first.text
        fingerprint = parse_keys(key_line())[0].fingerprint
        changed = client.post("/api/connection/test", json=payload(trust_fingerprint=fingerprint), headers=headers())
        assert changed.status_code == 200
        assert not changed.json()["ok"]
        assert "变化" in changed.json()["message"]
        assert len(scans) == 2 and not probes
