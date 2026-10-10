from fastapi.testclient import TestClient

from backend.config import Settings
from backend.server import create_app


def app(tmp_path):
    settings = Settings(tmp_path, "pi-star@radio.example.test", "https://api.example.test", "private-test-value", "test-model", "small", 8788)
    return create_app(settings, start_capture=False)


def test_status_contains_no_secret_and_limit_is_validated(tmp_path):
    with TestClient(app(tmp_path)) as client:
        response = client.get("/api/status")
        assert response.status_code == 200
        assert "private-test-value" not in response.text
        assert len(response.json()["slots"]) == 2
        assert client.get("/api/calls?limit=99999").status_code == 422


def test_audio_path_is_restricted_to_stored_call_ids(tmp_path):
    with TestClient(app(tmp_path)) as client:
        assert client.get("/api/audio/not-a-call").status_code == 422
        assert client.get("/api/audio/" + "a" * 32).status_code == 404
        assert client.get("/api/calls").json() == []


def test_named_and_legacy_recordings_keep_the_same_playback_urls(tmp_path):
    from backend.audio import write_recording
    from tests.test_audio import call
    application = app(tmp_path)
    with TestClient(application) as client:
        named = call(audio_url="/api/audio/" + "a" * 32, status="complete", duration=1)
        named["audio_filename"] = write_recording(tmp_path / "audio", named, bytes(16000))
        application.state.runtime.store.save(named)
        response = client.get(named["audio_url"])
        assert response.status_code == 200
        assert response.content[:4] == b"RIFF"
        from pathlib import Path
        assert Path(named["audio_filename"]).name in response.headers["content-disposition"]
        legacy = call(id="b" * 32, audio_url="/api/audio/" + "b" * 32, status="complete", duration=1)
        (tmp_path / "audio" / (legacy["id"] + ".wav")).write_bytes(response.content)
        application.state.runtime.store.save(legacy)
        assert client.get(legacy["audio_url"]).status_code == 200


def test_untrusted_stored_audio_filename_is_not_served(tmp_path):
    from tests.test_audio import call
    application = app(tmp_path)
    with TestClient(application) as client:
        record = call(status="complete", duration=1, audio_url="/api/audio/" + "a" * 32, audio_filename="../secret.wav")
        application.state.runtime.store.save(record)
        assert client.get(record["audio_url"]).status_code == 404
