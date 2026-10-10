"""Editable provider settings with write-only, privately persisted API keys."""

import json
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

import httpx

from backend.config import Settings
from backend.private_files import sync_directory, write_private

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslationDetails:
    base_url: str
    model: str
    api_mode: str
    enabled: bool
    api_key: str = field(default="", repr=False)


def canonical_base_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1024 or "?" in value or "#" in value or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("请输入有效的 HTTP(S) 接口地址")
    try:
        url = httpx.URL(value.strip())
        if url.scheme not in {"http", "https"} or not url.host or url.username or url.password or url.query or url.fragment or (url.port is not None and not 1 <= url.port <= 65535):
            raise ValueError("Invalid provider URL")
        path = url.raw_path.rstrip(b"/").removesuffix(b"/v1") + b"/v1"
        result = str(url.copy_with(raw_path=path))
        if len(result) > 1024:
            raise ValueError("Provider URL is too long")
        return result
    except (httpx.InvalidURL, ValueError):
        raise ValueError("接口地址必须为 HTTP(S)，不能包含用户名、密码、查询参数或片段") from None


def validate_translation(payload: dict) -> TranslationDetails:
    if not isinstance(payload, dict) or set(payload) - {"base_url", "model", "api_mode", "enabled", "api_key", "clear_api_key"}:
        raise ValueError("翻译设置包含不支持的字段")
    url = canonical_base_url(payload.get("base_url", ""))
    model = payload.get("model", "")
    mode = payload.get("api_mode", "responses")
    enabled = payload.get("enabled", True)
    key = payload.get("api_key", "")
    clear = payload.get("clear_api_key", False)
    if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(character) < 32 or ord(character) == 127 for character in model):
        raise ValueError("请输入有效的模型名称（最多 200 字符）")
    if not isinstance(mode, str) or mode not in {"responses", "chat_completions"}:
        raise ValueError("请选择 Responses 或 Chat Completions 接口")
    if not isinstance(enabled, bool) or not isinstance(clear, bool):
        raise TypeError("翻译开关或清除密钥选项无效")
    if not isinstance(key, str) or len(key) > 4096 or not key.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in key):
        raise ValueError("API 密钥格式无效")
    key = key.strip(" ")
    if clear and key:
        raise ValueError("不能同时输入新密钥并选择清除密钥")
    return TranslationDetails(url, model.strip(), mode, enabled, key)


def _document(directory: Path) -> dict | None:
    filename = directory / "translation.json"
    if filename.is_symlink():
        raise ValueError("保存的翻译设置路径无效")
    if not filename.exists():
        return None
    if filename.stat().st_size > 8192:
        raise ValueError("保存的翻译设置过大")
    try:
        document = json.loads(filename.read_text())
        if not isinstance(document, dict) or document.get("format") != 1 or not isinstance(document.get("generation"), str) or not re.fullmatch(r"[0-9a-f]{32}", document["generation"]) or not isinstance(document.get("has_key"), bool):
            raise ValueError("保存的翻译设置版本或路径无效")
        validate_translation({key:document[key] for key in ("base_url", "model", "api_mode", "enabled")})
        return document
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError):
        raise ValueError("保存的翻译设置无效") from None


def _generation(directory: Path, identifier: str) -> Path:
    private = directory / ".translation"
    result = private / identifier
    if private.is_symlink() or result.is_symlink() or not result.resolve().is_relative_to(directory.resolve()):
        raise ValueError("翻译凭据目录无效")
    return result


def translation_backup_files(directory: Path) -> list[Path]:
    document = _document(directory)
    if document is None:
        return []
    files = [directory / "translation.json"]
    generation = _generation(directory, document["generation"])
    if document["has_key"]:
        files.append(generation / "api_key")
    if any(path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()) for path in files):
        raise ValueError("翻译设置备份缺少凭据或路径无效")
    return files


class TranslationSettingsManager:
    def __init__(self, settings: Settings):
        self.base = settings
        self.root = settings.data_dir
        self.path = self.root / "translation.json"
        self.private_root = self.root / ".translation"

    def _key(self, document: dict | None) -> str:
        if document is None:
            return self.base.api_key
        directory = _generation(self.root, document["generation"])
        if not document["has_key"]:
            return ""
        filename = directory / "api_key"
        if filename.is_symlink() or not filename.is_file() or not os.access(filename, os.R_OK) or filename.stat().st_size > 4096:
            return ""
        try:
            key = filename.read_text()
        except (OSError, UnicodeError):
            return ""
        if not key.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in key):
            return ""
        return key

    def settings(self, base: Settings | None = None) -> Settings:
        base = base or self.base
        document = _document(self.root)
        if document is None:
            return replace(base, api_url=canonical_base_url(self.base.api_url), api_key=self.base.api_key,
                           translation_model=self.base.translation_model, translation_api=self.base.translation_api,
                           translation_enabled=self.base.translation_enabled)
        details = validate_translation({key:document[key] for key in ("base_url", "model", "api_mode", "enabled")})
        return replace(base, api_url=details.base_url, api_key=self._key(document), translation_model=details.model,
                       translation_api=details.api_mode, translation_enabled=details.enabled)

    def public(self) -> dict:
        settings = self.settings()
        return {"base_url":settings.api_url, "model":settings.translation_model, "api_mode":settings.translation_api,
                "enabled":settings.translation_enabled, "api_key_configured":bool(settings.api_key)}

    def draft(self, payload: dict) -> TranslationDetails:
        details = validate_translation(payload)
        current = self.settings()
        if not details.api_key and not payload.get("clear_api_key", False) and details.base_url == current.api_url:
            details = replace(details, api_key=current.api_key)
        if details.enabled and not details.api_key:
            raise ValueError("请输入 API 密钥；更改接口地址后需要重新填写，或先关闭中文翻译")
        return details

    def save(self, details: TranslationDetails) -> dict:
        previous = _document(self.root)
        self.private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.private_root.is_symlink():
            raise ValueError("翻译凭据目录无效")
        os.chmod(self.private_root, 0o700)
        identifier = uuid4().hex
        generation = _generation(self.root, identifier)
        generation.mkdir(mode=0o700)
        temporary = None
        committed = False
        try:
            if details.api_key:
                write_private(generation / "api_key", details.api_key.encode())
            sync_directory(generation)
            sync_directory(self.private_root)
            document = {"format":1, "generation":identifier, "has_key":bool(details.api_key), "base_url":details.base_url,
                        "model":details.model, "api_mode":details.api_mode, "enabled":details.enabled}
            descriptor, name = tempfile.mkstemp(prefix=".translation-", dir=self.root)
            temporary = Path(name)
            with os.fdopen(descriptor, "w") as handle:
                json.dump(document, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            committed = True
            sync_directory(self.root)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            if not committed:
                shutil.rmtree(generation)
        if previous:
            try:
                shutil.rmtree(_generation(self.root, previous["generation"]))
            except OSError:
                log.warning("Previous translation credential files could not be removed")
        return self.public()
