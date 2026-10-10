import asyncio
from dataclasses import replace

from backend.config import Settings
from backend.store import Store


class Supervisor:
    def __init__(self):
        self.tasks = []

    def spawn(self, name, coroutine, group):
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.append(task)
        return task

    async def cancel_group(self, group):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()


class Child:
    def __init__(self, settings):
        self.ready = False
        self.stopped = False

    async def start(self):
        self.ready = True

    async def transcribe(self, path, language_hint=None):
        return {"original":"hello", "language":"en", "speech_detected":True, "language_probability":.99}

    async def stop(self):
        self.stopped = True
        self.ready = False


def prepare(tmp_path, monkeypatch, **settings_changes):
    import backend.processing as module
    settings = replace(Settings(tmp_path, "", "https://example.invalid", "dummy", "model", "small", 8788), transcription_workers=1, translation_workers=1, retry_base_seconds=.01, **settings_changes)
    store = Store(tmp_path / "calls.sqlite3", min_duration=1)
    observed = []
    monkeypatch.setattr(module, "AsrClient", Child)

    class Translator:
        def __init__(self, *args, **kwargs): pass
        async def translate(self, original): return {"translated":"你好", "topic":"问候"}
        async def close(self): pass

    monkeypatch.setattr(module, "TranslationClient", Translator)
    return module.ProcessingPipeline(settings, store, observed.append, Supervisor()), store, observed


def enqueue(store, tmp_path, number=1):
    identifier = f"{number:032x}"
    path = tmp_path / "audio" / f"{identifier}.wav"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"audio")
    call = {"id":identifier, "started_at":1000+number, "status":"queued", "duration":1, "slot":number, "source_id":number, "original":None, "translated":None, "error":None, "audio_url":f"/api/audio/{identifier}"}
    store.enqueue_call(call, "transcription")
    return call


async def eventually(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.005)
    await asyncio.wait_for(poll(), timeout=2)


def test_pipeline_constructor_is_paused_and_does_not_consume_jobs(tmp_path, monkeypatch):
    pipeline, store, _ = prepare(tmp_path, monkeypatch)
    call = enqueue(store, tmp_path)
    assert pipeline.metrics()["transcription"] == "paused"
    assert store.get(call["id"])["status"] == "queued"
    assert pipeline.supervisor.tasks == []
    store.close()


def test_slow_translation_does_not_block_later_transcription(tmp_path, monkeypatch):
    pipeline, store, observed = prepare(tmp_path, monkeypatch)
    first, second = enqueue(store, tmp_path), enqueue(store, tmp_path, 2)

    async def run():
        release = asyncio.Event()
        async def translate(text):
            await release.wait()
            return {"translated":"你好", "topic":"问候"}
        pipeline.translator.translate = translate
        pipeline.start()
        try:
            await eventually(lambda: all(store.get(item["id"]).get("original") for item in (first, second)))
            assert not release.is_set()
            assert pipeline.metrics()["pending_translation"] + pipeline.metrics()["processing_translation"] == 2
            release.set()
            await eventually(lambda: all(store.get(item["id"])["status"] == "complete" for item in (first, second)))
        finally:
            await pipeline.stop()
        assert observed
        assert pipeline.metrics()["transcription"] == "paused"
    asyncio.run(run())
    store.close()


def test_stopping_returns_leases_without_failures_and_ignores_late_results(tmp_path, monkeypatch):
    pipeline, store, _ = prepare(tmp_path, monkeypatch, translation_enabled=False)
    call = enqueue(store, tmp_path)
    clients = []
    class Blocking(Child):
        def __init__(self, settings):
            super().__init__(settings)
            clients.append(self)
        async def transcribe(self, path, language_hint=None):
            await asyncio.Event().wait()
    monkeypatch.setattr("backend.processing.AsrClient", Blocking)

    async def run():
        pipeline.start()
        await eventually(lambda: store.job_counts()["transcription"]["running"] == 1)
        stale = store.connection.execute("SELECT id,lease_token FROM jobs").fetchone()
        await pipeline.stop()
        assert store.job_counts()["transcription"]["ready"] == 1
        assert clients[0].stopped
        assert store.connection.execute("SELECT attempts FROM jobs").fetchone()[0] == 0
        assert not store.finish_job(stale["id"], stale["lease_token"], {**call, "status":"complete"})
        assert store.get(call["id"])["status"] == "queued"
    asyncio.run(run())
    store.close()


def test_no_translation_key_finishes_as_original_only(tmp_path, monkeypatch):
    pipeline, store, _ = prepare(tmp_path, monkeypatch, api_key="")
    call = enqueue(store, tmp_path)

    async def run():
        pipeline.start()
        try:
            await eventually(lambda: store.get(call["id"])["status"] == "transcribed")
            assert store.job_counts()["translation"]["ready"] == 0
        finally:
            await pipeline.stop()
    asyncio.run(run())
    store.close()


def test_model_unavailability_leaves_pending_jobs_untouched(tmp_path, monkeypatch):
    pipeline, store, _ = prepare(tmp_path, monkeypatch)
    call = enqueue(store, tmp_path)
    class Unavailable(Child):
        async def start(self):
            from backend.asr_client import AsrError
            raise AsrError("model unavailable")
    monkeypatch.setattr("backend.processing.AsrClient", Unavailable)

    async def run():
        pipeline.start()
        await eventually(lambda: pipeline.metrics()["transcription"] == "unavailable")
        assert store.get(call["id"])["status"] == "queued"
        assert store.job_counts()["transcription"]["ready"] == 1
        await pipeline.stop()
    asyncio.run(run())
    store.close()


def test_translation_retries_are_bounded_and_permanent_errors_fail_once(tmp_path, monkeypatch):
    from backend.ai import AIRequestError
    pipeline, store, _ = prepare(tmp_path, monkeypatch, retry_attempts=2)
    record = enqueue(store, tmp_path)
    requests = []

    async def run():
        async def unavailable(text):
            requests.append(text)
            raise AIRequestError("HTTP 429", retryable=True, retry_after=.01)
        pipeline.translator.translate = unavailable
        pipeline.start()
        try:
            await eventually(lambda: store.get(record["id"])["status"] == "failed")
            assert len(requests) == 2
            assert store.get(record["id"])["original"] == "hello"
            assert store.job_counts()["translation"]["failed"] == 1
        finally:
            await pipeline.stop()
        another = enqueue(store, tmp_path, 2)
        requests.clear()
        async def unauthorized(text):
            requests.append(text)
            raise AIRequestError("HTTP 401", retryable=False)
        pipeline.translator.translate = unauthorized
        pipeline.start()
        try:
            await eventually(lambda: store.get(another["id"])["status"] == "failed")
            assert len(requests) == 1
        finally:
            await pipeline.stop()
    asyncio.run(run())
    store.close()
