"""Private, atomic radio connection settings; public views never contain passwords."""

import ipaddress
import json
import logging
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from backend.config import Settings
from backend.private_files import sync_directory as _sync_directory
from backend.private_files import write_private as _write_private

log = logging.getLogger(__name__)


class ConnectionConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class ConnectionDetails:
    host: str
    port: int
    username: str
    auth_mode: str
    password: str = field(default="", repr=False)

    @property
    def target(self):
        return f"{self.username}@{self.host}"

    @property
    def identity(self):
        return self.host, self.port, self.username, self.auth_mode


def validate_details(payload: dict) -> ConnectionDetails:
    if not isinstance(payload, dict) or set(payload) - {"host", "port", "username", "auth_mode", "password", "trust_fingerprint"}:
        raise ValueError("连接设置包含不支持的字段")
    host = payload.get("host", "")
    username = payload.get("username", "")
    port = payload.get("port", 22)
    mode = payload.get("auth_mode", "key")
    password = payload.get("password", "")
    if not isinstance(host, str) or not host.strip() or len(host) > 253:
        raise ValueError("请输入有效的设备地址")
    host = host.strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        address = ipaddress.ip_address(host)
        if isinstance(address, ipaddress.IPv6Address) and address.scope_id is not None:
            raise ValueError("Scoped IPv6 is not supported")
        host = str(address)
    except ValueError:
        if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in host.rstrip(".").split(".")):
            raise ValueError("设备地址只能是 IP 或主机名，端口请单独填写") from None
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", username):
        raise ValueError("请输入有效的 SSH 用户名")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("SSH 端口必须在 1 到 65535 之间")
    if not isinstance(mode, str) or mode not in {"key", "password"}:
        raise ValueError("请选择密钥或密码登录")
    if not isinstance(password, str) or len(password.encode()) > 4095 or any(character in password for character in "\r\n\0"):
        raise ValueError("SSH 密码格式无效")
    fingerprint = payload.get("trust_fingerprint", "")
    if not isinstance(fingerprint, str) or (fingerprint and not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint)):
        raise ValueError("设备指纹格式无效，请重新测试连接")
    return ConnectionDetails(host, port, username, mode, password if mode == "password" else "")


def connection_backup_files(directory: Path) -> list[Path]:
    """Require a complete, contained credential generation for backup/restore."""
    configuration = directory / "connection.json"
    if not configuration.exists():
        return []
    if configuration.is_symlink() or configuration.stat().st_size > 8192:
        raise ValueError("连接设置备份无效")
    document = json.loads(configuration.read_text())
    if not isinstance(document, dict) or document.get("format") != 1 or not isinstance(document.get("generation"), str) or not re.fullmatch(r"[0-9a-f]{32}", document["generation"]):
        raise ValueError("连接设置备份无效")
    validate_details({key:document.get(key) for key in ("host", "port", "username", "auth_mode")})
    private = directory / ".connection"
    generation = private / document["generation"]
    files = [configuration, generation / "known_hosts"]
    if document["auth_mode"] == "password":
        files.append(generation / "password")
    if private.is_symlink() or generation.is_symlink() or any(path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory.resolve()) for path in files):
        raise ValueError("连接凭据备份缺失或路径无效")
    return files


class ConnectionManager:
    def __init__(self, base_settings: Settings):
        self.base = base_settings
        self.root = base_settings.data_dir
        self.path = self.root / "connection.json"
        self.private_root = self.root / ".connection"

    def _document(self) -> dict | None:
        if not self.path.exists():
            return None
        if self.path.is_symlink() or self.path.stat().st_size > 8192:
            raise ValueError("保存的连接设置无效")
        try:
            value = json.loads(self.path.read_text())
            if not isinstance(value, dict) or value.get("format") != 1 or not re.fullmatch(r"[0-9a-f]{32}", value.get("generation", "")):
                raise ValueError("保存的连接设置版本或路径无效")
            validate_details({key:value[key] for key in ("host", "port", "username", "auth_mode")})
            self._generation(value["generation"])
            return value
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("保存的连接设置无效") from exc

    def _generation(self, identifier: str) -> Path:
        directory = self.private_root / identifier
        if self.private_root.is_symlink() or directory.is_symlink() or not directory.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("连接凭据目录无效")
        return directory

    def details(self) -> ConnectionDetails:
        value = self._document()
        if value:
            return validate_details({key:value[key] for key in ("host", "port", "username", "auth_mode")})
        username, separator, host = self.base.pi_host.rpartition("@")
        if not separator:
            host, username = self.base.pi_host, "pi-star"
        return ConnectionDetails(host.strip("[]"), self.base.ssh_port, username, "password" if self.base.ssh_password_file else "key")

    def settings(self) -> Settings:
        value = self._document()
        if not value:
            return self.base
        details = self.details()
        directory = self._generation(value["generation"])
        return self._settings(details, directory)

    def _settings(self, details: ConnectionDetails, directory: Path) -> Settings:
        if any((directory / name).is_symlink() for name in ("password", "known_hosts")):
            raise ValueError("连接凭据文件无效")
        return replace(self.base, pi_host=details.target, ssh_port=details.port,
                       ssh_key_file=self.base.ssh_key_file if details.auth_mode == "key" else None,
                       ssh_password_file=directory / "password" if details.auth_mode == "password" else None,
                       ssh_known_hosts_file=directory / "known_hosts")

    def key_available(self) -> bool:
        return bool(self.base.ssh_key_file and self.base.ssh_key_file.is_file() and self.base.ssh_key_file.stat().st_size and os.access(self.base.ssh_key_file, os.R_OK)) or not self.base.require_explicit_ssh

    def public(self) -> dict:
        details, settings = self.details(), self.settings()
        password_set = bool(settings.ssh_password_file and settings.ssh_password_file.is_file() and settings.ssh_password_file.stat().st_size and os.access(settings.ssh_password_file, os.R_OK))
        authentication = password_set if details.auth_mode == "password" else self.key_available()
        host_key = not self.base.require_explicit_ssh or bool(settings.ssh_known_hosts_file and settings.ssh_known_hosts_file.is_file() and settings.ssh_known_hosts_file.stat().st_size and os.access(settings.ssh_known_hosts_file, os.R_OK))
        return {"host":details.host, "port":details.port, "username":details.username, "auth_mode":details.auth_mode,
                "password_configured":password_set, "key_available":self.key_available(),
                "configured":bool(details.host and authentication and host_key)}

    def draft(self, payload: dict) -> ConnectionDetails:
        details = validate_details(payload)
        if details.auth_mode == "key" and not self.key_available():
            raise ValueError("未配置 SSH 身份密钥，请选择密码登录或在部署时提供密钥")
        if details.auth_mode == "password" and not details.password:
            if details.identity != self.details().identity:
                raise ValueError("更改设备或用户后，请重新输入 SSH 密码")
            filename = self.settings().ssh_password_file
            if not filename or not filename.is_file() or filename.is_symlink() or filename.stat().st_size > 4096:
                raise ValueError("请输入 SSH 密码")
            try:
                password = filename.read_text().removesuffix("\n").removesuffix("\r")
            except UnicodeError:
                raise ValueError("密码文件无法读取，请重新输入 SSH 密码") from None
            details = validate_details({**payload, "password":password})
            if not details.password:
                raise ValueError("请输入 SSH 密码")
        return details

    @contextmanager
    def temporary_settings(self, details: ConnectionDetails, host_key_line: str):
        with tempfile.TemporaryDirectory(prefix="starwave-connection-") as temporary:
            directory = Path(temporary)
            _write_private(directory / "known_hosts", host_key_line.encode())
            if details.auth_mode == "password":
                _write_private(directory / "password", details.password.encode())
            yield self._settings(details, directory)

    def save(self, details: ConnectionDetails, host_key_line: str) -> dict:
        previous = self._document()
        self.private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.private_root.is_symlink():
            raise ValueError("连接凭据目录无效")
        os.chmod(self.private_root, 0o700)
        generation = uuid4().hex
        directory = self._generation(generation)
        directory.mkdir(mode=0o700)
        temporary_path = None
        committed = False
        try:
            _write_private(directory / "known_hosts", host_key_line.encode())
            if details.auth_mode == "password":
                _write_private(directory / "password", details.password.encode())
            _sync_directory(directory)
            _sync_directory(self.private_root)
            document = {"format":1, "generation":generation, "host":details.host, "port":details.port,
                        "username":details.username, "auth_mode":details.auth_mode}
            descriptor, name = tempfile.mkstemp(prefix=".connection-", dir=self.root)
            temporary_path = Path(name)
            with os.fdopen(descriptor, "w") as handle:
                json.dump(document, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
            committed = True
            _sync_directory(self.root)
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)
            if not committed:
                shutil.rmtree(directory)
        if previous:
            old = self._generation(previous["generation"])
            try:
                shutil.rmtree(old)
            except OSError:
                log.warning("Previous connection credential files could not be removed")
        return self.public()
