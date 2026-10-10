import asyncio
import base64
import json
import stat
import struct
from dataclasses import replace

import pytest

from backend.config import Settings
from backend.connection import ConnectionManager, validate_details
from backend.connection_probe import parse_keys, prepare_connection


def configuration(tmp_path):
    return Settings(tmp_path, "pi-star@radio.local", "https://example.invalid", "", "test", "small", 8788, require_explicit_ssh=True)


def host_key(seed=1):
    algorithm = b"ssh-ed25519"
    blob = struct.pack(">I", len(algorithm)) + algorithm + struct.pack(">I", 32) + bytes([seed]) * 32
    return parse_keys("radio.local ssh-ed25519 " + base64.b64encode(blob).decode())[0]


def request(**changes):
    return {"host":"radio.local", "port":22, "username":"pi-star", "auth_mode":"password", "password":"test-only password", **changes}


def test_saved_password_is_private_never_returned_and_survives_restart(tmp_path):
    manager = ConnectionManager(configuration(tmp_path))
    details = manager.draft(request())
    public = manager.save(details, host_key().line(details))
    assert public["configured"] and public["password_configured"]
    assert details.password not in json.dumps(public)
    assert details.password not in repr(details)
    assert details.password not in manager.path.read_text()
    saved = manager.settings()
    assert saved.ssh_password_file.read_text() == details.password
    assert stat.S_IMODE(saved.ssh_password_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(saved.ssh_password_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(manager.path.stat().st_mode) == 0o600
    restarted = ConnectionManager(configuration(tmp_path))
    assert restarted.public() == public
    assert restarted.settings().pi_host == "pi-star@radio.local"
    assert restarted.settings().ssh_password_file.read_text() == details.password


def test_unreadable_mounted_identity_is_not_advertised_as_a_working_key(tmp_path, monkeypatch):
    key = tmp_path / "identity"
    key.write_text("synthetic identity")
    hosts = tmp_path / "hosts"
    hosts.write_text("synthetic hosts")
    manager = ConnectionManager(replace(configuration(tmp_path), ssh_key_file=key, ssh_known_hosts_file=hosts))
    monkeypatch.setattr("backend.connection.os.access", lambda *args: False)
    assert not manager.public()["key_available"]
    assert not manager.public()["configured"]


def test_password_reuse_is_bound_to_the_complete_connection_identity(tmp_path):
    manager = ConnectionManager(configuration(tmp_path))
    details = manager.draft(request())
    manager.save(details, host_key().line(details))
    assert manager.draft(request(password="")).password == details.password
    for change in ({"host":"other.local"}, {"port":2222}, {"username":"other"}):
        with pytest.raises(ValueError, match="重新输入"):
            manager.draft(request(password="", **change))


def test_atomic_configuration_commit_does_not_change_previous_credentials_on_failure(tmp_path, monkeypatch):
    manager = ConnectionManager(configuration(tmp_path))
    previous = manager.draft(request())
    manager.save(previous, host_key().line(previous))
    original = manager.path.read_bytes()
    original_password = manager.settings().ssh_password_file
    replacement = manager.draft(request(host="other.local", password="replacement test password"))
    monkeypatch.setattr("backend.connection.os.replace", lambda *args: (_ for _ in ()).throw(OSError("simulated commit failure")))
    with pytest.raises(OSError):
        manager.save(replacement, host_key(2).line(replacement))
    assert manager.path.read_bytes() == original
    assert original_password.read_text() == previous.password
    assert len(list(manager.private_root.iterdir())) == 1


def test_password_rotation_removes_the_previous_private_generation(tmp_path):
    manager = ConnectionManager(configuration(tmp_path))
    first = manager.draft(request())
    manager.save(first, host_key().line(first))
    old = manager.settings().ssh_password_file.parent
    next_details = manager.draft(request(password="next test password"))
    manager.save(next_details, host_key().line(next_details))
    assert not old.exists()
    assert manager.settings().ssh_password_file.read_text() == next_details.password


@pytest.mark.parametrize("change", [{"host":"-oProxyCommand=bad"}, {"host":"10.0.0.0/24"}, {"host":"name@host"},
                                  {"host":"host:2222"}, {"host":"a..b"}, {"username":"-flag"}, {"username":"a;b"},
                                  {"host":"fe80::1%eth0"}, {"host":"fe80::1%eth0\nother-host"}, {"host":"fe80::1%eth0,other-host"},
                                  {"port":True}, {"port":0}, {"port":22.5}, {"password":"bad\nsecret"},
                                  {"password":"bad\0secret"}, {"password":"x" * 4096}, {"auth_mode":"invalid"}])
def test_connection_inputs_cannot_become_ssh_options_or_unbounded_secrets(change):
    with pytest.raises(ValueError):
        validate_details(request(**change))


def test_ipv6_and_nonstandard_ports_produce_a_bound_known_hosts_entry():
    details = validate_details(request(host="[2001:db8::1]", port=2222))
    assert details.host == "2001:db8::1"
    assert host_key().line(details).startswith("[2001:db8::1]:2222 ")


def test_unknown_or_changed_host_keys_are_not_authenticated_before_explicit_trust(tmp_path, monkeypatch):
    manager = ConnectionManager(configuration(tmp_path))
    key = host_key()

    async def output(*arguments):
        assert arguments[0] == "ssh-keyscan"
        return key.line(validate_details(request()))

    monkeypatch.setattr("backend.connection_probe.bounded_output", output)
    details, accepted, result = asyncio.run(prepare_connection(manager, request()))
    assert accepted is None
    assert result["needs_trust"] and result["host_key"]["fingerprint"] == key.fingerprint
    assert details.password not in json.dumps(result)
    assert not manager.path.exists()
    _, accepted, result = asyncio.run(prepare_connection(manager, request(trust_fingerprint=key.fingerprint)))
    assert accepted == key and result is None
    _, accepted, result = asyncio.run(prepare_connection(manager, request(trust_fingerprint=host_key(2).fingerprint)))
    assert accepted is None and not result["ok"]


def test_existing_verified_host_key_does_not_require_reconfirmation(tmp_path, monkeypatch):
    key = host_key()
    filename = tmp_path / "trusted_hosts"
    filename.write_text(key.line(validate_details(request())))
    manager = ConnectionManager(replace(configuration(tmp_path), ssh_known_hosts_file=filename))

    async def output(*arguments):
        return filename.read_text()

    monkeypatch.setattr("backend.connection_probe.bounded_output", output)
    _, accepted, result = asyncio.run(prepare_connection(manager, request()))
    assert accepted == key and result is None


def test_temporary_probe_credentials_are_removed_and_do_not_change_saved_settings(tmp_path):
    manager = ConnectionManager(configuration(tmp_path))
    details = manager.draft(request())
    with manager.temporary_settings(details, host_key().line(details)) as settings:
        temporary = settings.ssh_password_file.parent
        assert settings.ssh_password_file.read_text() == details.password
        assert settings.ssh_known_hosts_file.is_file()
    assert not temporary.exists()
    assert not manager.path.exists()


def test_corrupt_or_symlinked_saved_settings_do_not_fall_back_to_environment(tmp_path):
    manager = ConnectionManager(configuration(tmp_path))
    manager.path.write_text('[]')
    with pytest.raises(ValueError):
        manager.settings()
    manager.path.unlink()
    other = tmp_path / "other"
    other.write_text('{}')
    manager.path.symlink_to(other)
    with pytest.raises(ValueError):
        manager.settings()


def test_complete_backup_restores_private_connection_and_rejects_missing_password(tmp_path):
    from backend.maintenance import backup_data, restore_data
    from backend.store import Store

    source = tmp_path / "data"
    source.mkdir()
    store = Store(source / "calls.sqlite3")
    store.close()
    manager = ConnectionManager(configuration(source))
    details = manager.draft(request())
    manager.save(details, host_key().line(details))
    backup = tmp_path / "backup"
    backup_data(source, backup)
    restored = tmp_path / "restored"
    restored.mkdir()
    restore_data(backup, restored)
    recovered = ConnectionManager(configuration(restored))
    assert recovered.public() == manager.public()
    assert recovered.settings().ssh_password_file.read_text() == details.password
    assert stat.S_IMODE(recovered.settings().ssh_password_file.stat().st_mode) == 0o600
    manifest = json.loads((backup / "backup-manifest.json").read_text())
    manifest["files"] = [entry for entry in manifest["files"] if not entry["path"].endswith("/password")]
    (backup / "backup-manifest.json").write_text(json.dumps(manifest))
    another = tmp_path / "another"
    another.mkdir()
    with pytest.raises(ValueError, match="credential"):
        restore_data(backup, another)
    assert list(another.iterdir()) == []
