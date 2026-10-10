import asyncio
import os
import sys

import pytest

from backend.config import Settings

FAKE_CHILD = '''
import json, os, signal, sys, time
config = json.loads(sys.stdin.readline())
if config["model"] == "fail":
    print(json.dumps({"type":"error", "code":"model_load_failed", "retryable":True}), flush=True)
    sys.exit(2)
print(json.dumps({"type":"ready"}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    if config["model"] == "hang": time.sleep(60)
    if config["model"] == "ignoreterm":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
    if config["model"] == "huge":
        print("x" * 70000, flush=True)
        continue
    leaked = any(name in os.environ for name in ("OPENAI_API_KEY", "CONTROL_TOKEN", "OPENAI_API_KEY_FILE"))
    cache_ok = os.environ.get("HF_HOME") == os.path.join(os.path.abspath(config["cache_dir"]), "huggingface")
    threads_ok = os.environ.get("OPENBLAS_NUM_THREADS") == str(config["threads"])
    result = {"original":"leaked" if leaked else "hello" if cache_ok and threads_ok else "missing_settings", "language":"en", "speech_detected":True, "language_probability":.99}
    print(json.dumps({"type":"result", "request_id":request["request_id"], "result":result}), flush=True)
'''


def setup_client(tmp_path, model="small", **kwargs):
    from backend.asr_client import AsrClient
    script = tmp_path / "child.py"
    script.write_text(FAKE_CHILD)
    settings = Settings(tmp_path / "data", "", "https://example.invalid", "dummy", "model", model, 8788)
    audio = settings.data_dir / "audio" / "test.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"test")
    return AsrClient(settings, command=[sys.executable, "-u", str(script)], **kwargs), audio


def test_real_ipc_excludes_application_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy-secret")
    monkeypatch.setenv("CONTROL_TOKEN", "dummy-control")
    monkeypatch.setenv("OPENAI_API_KEY_FILE", "/never/read/this")
    client, path = setup_client(tmp_path)

    async def run():
        await client.start()
        try:
            assert (await client.transcribe(path))["original"] == "hello"
        finally:
            await client.stop()
        assert client.process is None

    asyncio.run(run())


def test_stop_kills_a_running_model_job(tmp_path):
    from backend.asr_client import AsrError
    client, path = setup_client(tmp_path, model="hang")

    async def run():
        await client.start()
        pid = client.process.pid
        task = asyncio.create_task(client.transcribe(path))
        await asyncio.sleep(.03)
        await client.stop()
        with pytest.raises(AsrError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())


@pytest.mark.parametrize("model", ["huge", "hang"])
def test_bad_output_and_job_timeout_are_bounded_and_reap_child(tmp_path, model):
    from backend.asr_client import AsrError
    client, path = setup_client(tmp_path, model=model, job_timeout=.05)

    async def run():
        await client.start()
        with pytest.raises(AsrError):
            await client.transcribe(path)
        assert client.process is None

    asyncio.run(run())


def test_model_load_failure_does_not_leave_a_child(tmp_path):
    from backend.asr_client import AsrError
    client, _ = setup_client(tmp_path, model="fail")

    async def run():
        with pytest.raises(AsrError):
            await client.start()
        assert client.process is None

    asyncio.run(run())


def test_job_path_must_be_within_audio_directory(tmp_path):
    from backend.asr_client import AsrError
    client, _ = setup_client(tmp_path)
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"test")

    async def run():
        with pytest.raises(AsrError):
            await client.transcribe(outside)
        assert client.process is None

    asyncio.run(run())


def test_stop_escalates_to_kill_when_child_ignores_termination(tmp_path):
    from backend.asr_client import AsrError
    client, path = setup_client(tmp_path, model="ignoreterm")

    async def run():
        await client.start()
        pid = client.process.pid
        task = asyncio.create_task(client.transcribe(path))
        await asyncio.sleep(.03)
        await asyncio.wait_for(client.stop(), timeout=4.5)
        with pytest.raises(AsrError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(run())


def test_actual_worker_protocol_passes_single_worker_settings_and_rejects_outside_paths(tmp_path, monkeypatch):
    import io
    import json

    from backend import asr_worker
    observed = {}
    class Model:
        def __init__(self, model, cache_dir, **kwargs):
            observed.update(kwargs)
        def load(self): pass
        def transcribe(self, path, hint):
            return {"original":"你好", "language":"zh", "speech_detected":True}
    monkeypatch.setattr(asr_worker, "Transcriber", Model)
    root = tmp_path / "audio"
    configuration = {"model":"small", "cache_dir":str(tmp_path / "models"), "audio_root":str(root), "device":"cpu", "compute_type":"int8", "threads":3}
    requests = [configuration, {"type":"transcribe", "request_id":"good", "path":str(root / "test.wav")}, {"type":"transcribe", "request_id":"bad", "path":str(tmp_path / "outside.wav")}]
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(json.dumps(value) + "\n" for value in requests)))
    monkeypatch.setattr(sys, "stdout", output)
    asr_worker.main()
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert messages[0]["type"] == "ready"
    assert messages[1]["result"]["original"] == "你好"
    assert messages[2]["type"] == "error"
    assert not messages[2]["retryable"]
    assert observed["workers"] == 1
    assert observed["threads"] == 3
