"""Private speech-model worker. It never reads application settings or secrets."""
import json
import sys
from pathlib import Path

from backend.ai import Transcriber

MAX_MESSAGE = 65536


def emit(value):
    encoded = json.dumps(value, ensure_ascii=False)
    if len(encoded.encode()) + 1 > MAX_MESSAGE:
        encoded = json.dumps({"type":"error", "code":"result_too_large", "retryable":False})
    print(encoded, flush=True)


def read_message():
    line = sys.stdin.readline(MAX_MESSAGE + 1)
    if not line:
        return None
    if len(line.encode()) > MAX_MESSAGE:
        raise ValueError("IPC request is too large")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise TypeError("IPC request must be an object")
    return value


def main():
    try:
        configuration = read_message()
        if configuration is None:
            return
        root = Path(configuration["audio_root"]).resolve()
        model = Transcriber(
            configuration["model"], Path(configuration["cache_dir"]),
            device=configuration["device"], compute_type=configuration["compute_type"],
            threads=int(configuration["threads"]), workers=1,
        )
        model.load()
    # Native/model libraries expose different exception types; IPC discloses only the type.
    except Exception as exc:  # noqa: BLE001
        emit({"type":"error", "code":"model_load_failed", "error_type":type(exc).__name__, "retryable":True})
        return
    emit({"type":"ready"})
    while True:
        try:
            request = read_message()
            if request is None:
                return
            path = Path(request["path"]).resolve()
            if request.get("type") != "transcribe" or not path.is_relative_to(root) or path.suffix != ".wav":
                raise ValueError("Invalid audio request")
            result = model.transcribe(path, request.get("language_hint"))
            emit({"type":"result", "request_id":request["request_id"], "result":result})
        except (ValueError, TypeError, KeyError, OSError) as exc:
            emit({"type":"error", "code":"audio_input_failed", "error_type":type(exc).__name__, "retryable":False})
        except Exception as exc:  # noqa: BLE001 -- isolate native model failures
            emit({"type":"error", "code":"transcription_failed", "error_type":type(exc).__name__, "retryable":True})


if __name__ == "__main__":
    main()
