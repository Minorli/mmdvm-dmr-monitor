from dataclasses import replace

import pytest

from backend.config import Settings, _secret


def settings(tmp_path):
    return Settings(tmp_path, "pi-star@radio.local", "https://api.example.test", "private", "test-model", "small", 8788)


def test_secret_is_not_in_configuration_repr(tmp_path):
    assert "private" not in repr(settings(tmp_path))


@pytest.mark.parametrize("field,value", [("pi_host", "-oProxyCommand=bad"), ("pi_host", "radio;bad"), ("port", 0), ("min_call_seconds", 0), ("transcription_workers", 0), ("audio_retention_days", -1), ("api_url", "https://key@api.example.test"), ("translation_api", "unknown")])
def test_invalid_configuration_fails_fast(tmp_path, field, value):
    with pytest.raises(ValueError):
        replace(settings(tmp_path), **{field: value})


def test_secret_file_and_environment_are_mutually_exclusive(tmp_path, monkeypatch):
    path = tmp_path / "token"
    path.write_text("private-token\n")
    monkeypatch.setenv("CONTROL_TOKEN_FILE", str(path))
    monkeypatch.delenv("CONTROL_TOKEN", raising=False)
    assert _secret("CONTROL_TOKEN") == "private-token"
    monkeypatch.setenv("CONTROL_TOKEN", "another-token")
    with pytest.raises(ValueError):
        _secret("CONTROL_TOKEN")
