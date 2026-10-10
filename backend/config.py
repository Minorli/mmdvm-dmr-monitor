"""Validated configuration; secrets are read only by the backend."""

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    # Keep positional compatibility with the original local installation.
    data_dir: Path
    pi_host: str
    api_url: str
    api_key: str = field(repr=False)
    translation_model: str
    whisper_model: str
    port: int
    min_call_seconds: float = 1.0
    cache_dir: Path = ROOT / ".cache/whisper"
    bind_host: str = "127.0.0.1"
    device_name: str = "Pi-Star"
    ssh_key_file: Path | None = None
    ssh_known_hosts_file: Path | None = None
    ssh_port: int = 22
    ssh_password_file: Path | None = field(default=None, repr=False)
    control_token: str = field(default="", repr=False)
    timezone: str = "Asia/Shanghai"
    transcription_workers: int = 2
    translation_workers: int = 2
    whisper_threads: int = 4
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    translation_api: str = "responses"
    request_timeout: float = 60.0
    retry_attempts: int = 4
    retry_base_seconds: float = 5.0
    job_lease_seconds: float = 300.0
    live_job_age_seconds: float = 300.0
    capture_timeout: float = 20.0
    reconnect_seconds: float = 5.0
    max_segment_seconds: float = 20.0
    audio_retention_days: int = 0
    translation_enabled: bool = True
    web_dist: Path = ROOT / "web/dist"
    require_explicit_ssh: bool = False

    def __post_init__(self):
        if self.pi_host and (
            not re.fullmatch(r"[A-Za-z0-9_.@:\[\]-]+", self.pi_host)
            or self.pi_host.startswith("-")
            or self.pi_host.count("@") > 1
            or ("@" in self.pi_host and ":" in self.pi_host.split("@")[0])
        ):
            raise ValueError("PI_STAR_HOST must be an SSH hostname or user@hostname")
        url = urlsplit(self.api_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("OPENAI_BASE_URL must be an HTTP(S) URL without embedded credentials")
        if not 1 <= self.port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        if not isinstance(self.ssh_port, int) or isinstance(self.ssh_port, bool) or not 1 <= self.ssh_port <= 65535:
            raise ValueError("SSH_PORT must be between 1 and 65535")
        if not 0 < self.min_call_seconds <= self.max_segment_seconds:
            raise ValueError("MIN_CALL_SECONDS must be positive and no longer than MAX_SEGMENT_SECONDS")
        for name in ("transcription_workers", "translation_workers", "whisper_threads", "retry_attempts"):
            if not 1 <= getattr(self, name) <= 32:
                raise ValueError(f"{name.upper()} must be between 1 and 32")
        for name in ("request_timeout", "retry_base_seconds", "job_lease_seconds", "live_job_age_seconds", "capture_timeout", "reconnect_seconds"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} must be positive")
        if self.audio_retention_days < 0:
            raise ValueError("AUDIO_RETENTION_DAYS cannot be negative")
        if self.whisper_device not in {"cpu", "cuda", "auto"}:
            raise ValueError("WHISPER_DEVICE must be cpu, cuda, or auto")
        if self.translation_api not in {"responses", "chat_completions"}:
            raise ValueError("TRANSLATION_API must be responses or chat_completions")
        if self.control_token and not self.control_token.isascii():
            raise ValueError("CONTROL_TOKEN must contain only ASCII characters")
        if self.api_key and not self.api_key.isascii():
            raise ValueError("OPENAI_API_KEY must contain only ASCII characters")
        ZoneInfo(self.timezone)


def _secret(name: str) -> str:
    value = os.getenv(name, "")
    filename = os.getenv(name + "_FILE", "")
    if value and filename:
        raise ValueError(f"Set either {name} or {name}_FILE, not both")
    if filename:
        try:
            value = Path(filename).read_text().strip()
        except OSError as exc:
            raise ValueError(f"Cannot read {name}_FILE") from exc
        if not value:
            raise ValueError(f"{name}_FILE is empty")
    return value


def load_settings() -> Settings:
    # Environment overrides the project's explicitly configured .env defaults.
    load_dotenv(ROOT / ".env", override=False)
    for name in ("DATA_DIR", "CACHE_DIR", "WEB_DIST"):
        if name in os.environ and not os.environ[name].strip():
            raise ValueError(f"{name} cannot be empty")
    return Settings(
        data_dir=Path(os.getenv("DATA_DIR", str(ROOT / "data"))).resolve(),
        cache_dir=Path(os.getenv("CACHE_DIR", str(ROOT / ".cache/whisper"))).resolve(),
        pi_host=os.getenv("PI_STAR_HOST", ""),
        api_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com").rstrip("/"),
        api_key=_secret("OPENAI_API_KEY"),
        translation_model=os.getenv("TRANSLATION_MODEL", "gpt-4.1-mini"),
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        port=int(os.getenv("PORT", "8788")),
        min_call_seconds=float(os.getenv("MIN_CALL_SECONDS", "1")),
        bind_host=os.getenv("BIND_HOST", "127.0.0.1"),
        device_name=os.getenv("DEVICE_NAME", "Pi-Star"),
        ssh_key_file=Path(os.environ["SSH_KEY_FILE"]) if os.getenv("SSH_KEY_FILE") else None,
        ssh_known_hosts_file=Path(os.environ["SSH_KNOWN_HOSTS_FILE"]) if os.getenv("SSH_KNOWN_HOSTS_FILE") else None,
        ssh_port=int(os.getenv("SSH_PORT", "22")),
        ssh_password_file=Path(os.environ["SSH_PASSWORD_FILE"]) if os.getenv("SSH_PASSWORD_FILE") else None,
        control_token=_secret("CONTROL_TOKEN"),
        timezone=os.getenv("RECORDING_TIMEZONE", "Asia/Shanghai"),
        transcription_workers=int(os.getenv("TRANSCRIPTION_WORKERS", "2")),
        translation_workers=int(os.getenv("TRANSLATION_WORKERS", "2")),
        whisper_threads=int(os.getenv("WHISPER_THREADS", "4")),
        whisper_device=os.getenv("WHISPER_DEVICE", "cpu"),
        whisper_compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
        translation_api=os.getenv("TRANSLATION_API", "responses"),
        translation_enabled=os.getenv("TRANSLATION_ENABLED", "true").lower() in {"1", "true", "yes"},
        request_timeout=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60")),
        retry_attempts=int(os.getenv("RETRY_ATTEMPTS", "4")),
        retry_base_seconds=float(os.getenv("RETRY_BASE_SECONDS", "5")),
        job_lease_seconds=float(os.getenv("JOB_LEASE_SECONDS", "300")),
        live_job_age_seconds=float(os.getenv("LIVE_JOB_AGE_SECONDS", "300")),
        capture_timeout=float(os.getenv("CAPTURE_TIMEOUT_SECONDS", "20")),
        reconnect_seconds=float(os.getenv("RECONNECT_SECONDS", "5")),
        max_segment_seconds=float(os.getenv("MAX_SEGMENT_SECONDS", "20")),
        audio_retention_days=int(os.getenv("AUDIO_RETENTION_DAYS", "0")),
        web_dist=Path(os.getenv("WEB_DIST", str(ROOT / "web/dist"))).resolve(),
        require_explicit_ssh=os.getenv("REQUIRE_EXPLICIT_SSH", "false").lower() in {"1", "true", "yes"},
    )
