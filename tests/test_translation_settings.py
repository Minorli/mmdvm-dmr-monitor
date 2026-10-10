import asyncio
import json
import stat

import pytest

from backend.config import Settings
from backend.connection import validate_details
from backend.maintenance import backup_data, restore_data
from backend.runtime import Runtime
from backend.store import Store
from backend.translation_settings import (
    TranslationSettingsManager,
    canonical_base_url,
    validate_translation,
)


def settings(tmp_path):
    return Settings(tmp_path, "pi-star@radio.test", "https://provider.example.test", "dummy-env-key", "old-model", "small", 8788)


def payload(**changes):
    return {"base_url":"https://provider.example.test/v1", "model":"new-model", "api_mode":"responses", "enabled":True, **changes}


def test_same_provider_keeps_secret_but_changing_provider_requires_a_new_one(tmp_path):
    manager = TranslationSettingsManager(settings(tmp_path))
    draft = manager.draft(payload())
    assert draft.api_key == "dummy-env-key"
    assert draft.api_key not in repr(draft)
    for change in ("https://other.example.test/v1", "https://provider.example.test/gateway/v1", "http://provider.example.test/v1"):
        with pytest.raises(ValueError, match="重新填写"):
            manager.draft(payload(base_url=change))
    changed = manager.draft(payload(base_url="https://other.example.test", api_key="dummy-new-key"))
    assert changed.api_key == "dummy-new-key"
    disabled = manager.draft(payload(base_url="https://other.example.test", enabled=False))
    assert disabled.api_key == ""


def test_provider_normalization_preserves_paths_and_key_binding():
    assert canonical_base_url(" HTTPS://PROVIDER.EXAMPLE.TEST:443/v1/ ") == "https://provider.example.test/v1"
    assert canonical_base_url("https://provider.example.test") == "https://provider.example.test/v1"
    assert canonical_base_url("https://provider.example.test/a/../gateway") == "https://provider.example.test/gateway/v1"
    assert canonical_base_url("https://provider.example.test/gateway%2Fspecial") == "https://provider.example.test/gateway%2Fspecial/v1"


@pytest.mark.parametrize("changes", [{"base_url":"file:///tmp/provider"}, {"base_url":"https://user:password@provider.test"},
                                   {"base_url":"https://provider.test?secret=x"}, {"base_url":"https://provider.test?"},
                                   {"base_url":"https://provider.test#"}, {"base_url":"https://provider.test:99999"},
                                   {"base_url":"https://provider.test\nextra"}, {"base_url":"https://provider.test/" + "汉" * 200},
                                   {"model":[]}, {"model":"x" * 201}, {"model":"model\nsecret"}, {"model":""},
                                   {"api_mode":{}}, {"enabled":1}, {"clear_api_key":"true"},
                                   {"api_key":"key\n"}, {"api_key":"key\0"}, {"api_key":"密钥"}, {"api_key":"x" * 4097}])
def test_bad_translation_inputs_fail_before_any_provider_call(changes):
    with pytest.raises((ValueError, TypeError)):
        validate_translation(payload(**changes))


def test_private_key_persists_without_appearing_in_metadata_public_views_or_repr(tmp_path):
    manager = TranslationSettingsManager(settings(tmp_path))
    details = manager.draft(payload(api_key="saved-dummy-key"))
    public = manager.save(details)
    assert public["api_key_configured"]
    assert "api_key" not in public and details.api_key not in json.dumps(public)
    assert details.api_key not in manager.path.read_text()
    assert stat.S_IMODE(manager.path.stat().st_mode) == 0o600
    document = json.loads(manager.path.read_text())
    keyfile = manager.private_root / document["generation"] / "api_key"
    assert keyfile.read_text() == details.api_key
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert stat.S_IMODE(keyfile.parent.stat().st_mode) == 0o700
    restarted = TranslationSettingsManager(settings(tmp_path))
    assert restarted.settings().api_key == details.api_key
    assert restarted.public() == public


def test_clear_or_missing_private_key_never_falls_back_to_environment(tmp_path):
    manager = TranslationSettingsManager(settings(tmp_path))
    manager.save(manager.draft(payload(api_key="saved-dummy-key")))
    document = json.loads(manager.path.read_text())
    (manager.private_root / document["generation"] / "api_key").unlink()
    assert manager.settings().api_key == ""
    assert not manager.public()["api_key_configured"]
    manager.save(manager.draft(payload(enabled=False, clear_api_key=True)))
    assert manager.settings().api_key == ""
    assert not TranslationSettingsManager(settings(tmp_path)).public()["api_key_configured"]
    with pytest.raises(ValueError):
        manager.draft(payload(clear_api_key=True))


def test_failure_to_commit_metadata_preserves_the_previous_secret_and_provider(tmp_path, monkeypatch):
    manager = TranslationSettingsManager(settings(tmp_path))
    manager.save(manager.draft(payload(api_key="saved-dummy-key")))
    previous = manager.path.read_bytes()
    monkeypatch.setattr("backend.translation_settings.os.replace", lambda *args: (_ for _ in ()).throw(OSError("simulated write failure")))
    with pytest.raises(OSError):
        manager.save(manager.draft(payload(base_url="https://other.example.test", api_key="replacement-dummy-key")))
    assert manager.path.read_bytes() == previous
    assert manager.settings().api_key == "saved-dummy-key"
    assert len(list(manager.private_root.iterdir())) == 1


def test_runtime_alternates_radio_and_provider_changes_without_resetting_either(tmp_path, monkeypatch):
    runtime = Runtime(settings(tmp_path))
    closed = []

    async def close_previous():
        closed.append(True)

    runtime.pipeline.translator.close = close_previous
    radio = validate_details({"host":"other-radio.test", "port":2222, "username":"operator", "auth_mode":"password", "password":"dummy-radio-password"})
    class Key:
        def line(self, details):
            return "other-radio.test ssh-ed25519 synthetic-test-only\n"
    async def prepare(*args):
        return radio, Key(), None
    monkeypatch.setattr("backend.runtime.prepare_connection", prepare)

    async def run():
        await runtime.configure_translation(payload(base_url="https://other-provider.example.test", api_key="new-dummy-key", api_mode="chat_completions"))
        assert closed == [True]
        await runtime.configure_connection({})
        assert runtime.settings.pi_host == radio.target and runtime.settings.ssh_port == 2222
        assert runtime.settings.api_url == "https://other-provider.example.test/v1"
        assert runtime.pipeline.translator.url == "https://other-provider.example.test/v1/chat/completions"
        assert runtime.pipeline.translator.key == "new-dummy-key"
        assert runtime.pipeline.translator.model == "new-model"
        assert runtime.pipeline.translation_enabled and not runtime.pipeline.running
        await runtime.configure_translation(payload(base_url="https://other-provider.example.test", model="another-model", api_mode="responses"))
        assert runtime.settings.pi_host == radio.target
        assert runtime.pipeline.translator.model == "another-model"
        assert runtime.pipeline.translator.key == "new-dummy-key"
        assert runtime.supervisor.tasks == {}
        await runtime.stop()
    asyncio.run(run())
    restarted = Runtime(settings(tmp_path))
    assert restarted.settings.pi_host == radio.target
    assert restarted.settings.translation_model == "another-model"
    assert restarted.settings.api_key == "new-dummy-key"
    restarted.store.close()


def test_complete_backup_restores_provider_secret_and_rejects_an_incomplete_manifest(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    Store(data / "calls.sqlite3").close()
    manager = TranslationSettingsManager(settings(data))
    manager.save(manager.draft(payload(api_key="backup-dummy-key")))
    backup = tmp_path / "backup"
    backup_data(data, backup)
    restored = tmp_path / "restored"
    restored.mkdir()
    restore_data(backup, restored)
    assert TranslationSettingsManager(settings(restored)).settings().api_key == "backup-dummy-key"
    manifest = json.loads((backup / "backup-manifest.json").read_text())
    manifest["files"] = [entry for entry in manifest["files"] if not entry["path"].endswith("/api_key")]
    (backup / "backup-manifest.json").write_text(json.dumps(manifest))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="credential"):
        restore_data(backup, other)
    assert list(other.iterdir()) == []
