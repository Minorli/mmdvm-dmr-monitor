import pytest

from backend.ai import parse_translation, response_text


def test_reads_responses_api_output():
    assert response_text({"output": [{"content": [{"type": "output_text", "text": "你好"}]}]}) == "你好"


def test_translation_requires_real_text_and_accepts_json_fence():
    assert parse_translation('```json\n{"translated":"晚上好","topic":"信号测试"}\n```') == {"translated": "晚上好", "topic": "信号测试"}


@pytest.mark.parametrize("text", ["{}", '{"translated":42}', '{"translated":""}', "not json"])
def test_invalid_model_output_is_not_displayed_as_a_translation(text):
    with pytest.raises(ValueError):
        parse_translation(text)


def test_transcriber_can_decode_our_8khz_wav_format(tmp_path):
    import wave

    from faster_whisper.audio import decode_audio
    path = tmp_path / "call.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(bytes(16000))
    decoded = decode_audio(str(path), sampling_rate=16000)
    assert len(decoded) == 16000
    assert not decoded.any()


def test_silent_radio_tail_skips_language_detection_and_transcription(tmp_path):
    import wave

    from backend.ai import Transcriber
    path = tmp_path / "silent.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(bytes(17280))
    transcriber = Transcriber("small", tmp_path)

    def unexpected_load():
        raise AssertionError("Silent audio must not run the speech model")

    transcriber.load = unexpected_load
    assert transcriber.transcribe(path) == {"original":"", "language":None, "speech_detected":False}


def test_chinese_recognition_is_not_biased_by_an_english_prompt(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import faster_whisper.audio
    import faster_whisper.vad
    import numpy as np

    from backend.ai import Transcriber

    monkeypatch.setattr(faster_whisper.audio, "decode_audio", lambda *args, **kwargs: np.zeros(16000))
    monkeypatch.setattr(faster_whisper.vad, "get_speech_timestamps", lambda *args: [{"start":0,"end":16000}])
    observed = {}

    def transcribe(audio, **kwargs):
        observed.update(kwargs)
        return [SimpleNamespace(text="你好", no_speech_prob=0.1)], SimpleNamespace(language="zh", language_probability=.99)

    model = Transcriber("small", tmp_path)
    model.model = SimpleNamespace(transcribe=transcribe)
    assert model.transcribe(tmp_path / "call.wav", "zh")["original"] == "你好"
    assert observed["language"] == "zh"
    assert not observed.get("initial_prompt")
