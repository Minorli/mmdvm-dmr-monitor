"""Verify an SSH host key before sending credentials, then test capture permissions."""

import asyncio
import base64
import contextlib
import hashlib
import struct
from dataclasses import dataclass

from backend.capture import CaptureError, SSHCapture
from backend.connection import ConnectionDetails, ConnectionManager

PROBE_SCRIPT = b'''import json, os, socket
if os.geteuid() != 0:
    raise RuntimeError("Capture permission requires root")
capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
capture.close()
print(json.dumps({"type":"ready"}), flush=True)
'''


@dataclass(frozen=True)
class HostKey:
    algorithm: str
    encoded: str
    fingerprint: str

    def line(self, details: ConnectionDetails) -> str:
        target = details.host if details.port == 22 else f"[{details.host}]:{details.port}"
        return f"{target} {self.algorithm} {self.encoded}\n"

    def public(self) -> dict:
        return {"algorithm":self.algorithm, "fingerprint":self.fingerprint}


def parse_keys(contents: str) -> list[HostKey]:
    keys = []
    for line in contents.splitlines():
        fields = line.split()
        if len(fields) < 3 or line.startswith("#") or fields[1] not in {"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"}:
            continue
        try:
            blob = base64.b64decode(fields[2], validate=True)
            length = struct.unpack(">I", blob[:4])[0]
            if not 0 < length < 128 or blob[4:4+length].decode() != fields[1] or len(blob) > 8192:
                continue
        except (ValueError, UnicodeDecodeError, struct.error):
            continue
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")
        keys.append(HostKey(fields[1], fields[2], fingerprint))
    return keys


async def bounded_output(*command: str) -> str:
    process = None
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL))
    try:
        process = await asyncio.shield(spawn)
        async with asyncio.timeout(12):
            output = bytearray()
            while chunk := await process.stdout.read(4096):
                output.extend(chunk)
                if len(output) > 32768:
                    raise ValueError("设备返回的数据过大")
            await process.wait()
        return output.decode(errors="replace")
    finally:
        if process is None:
            process = await spawn
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 2)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()


async def prepare_connection(manager: ConnectionManager, payload: dict) -> tuple[ConnectionDetails, HostKey | None, dict | None]:
    details = manager.draft(payload)
    try:
        scanned = parse_keys(await bounded_output("ssh-keyscan", "-T", "5", "-p", str(details.port), "-t", "ed25519,ecdsa,rsa", details.host))
    except (OSError, TimeoutError):
        return details, None, {"ok":False, "message":"无法取得设备 SSH 指纹，请检查地址、端口和网络连接"}
    if not scanned:
        return details, None, {"ok":False, "message":"设备没有返回可用的 SSH 主机密钥，请检查地址和端口"}
    target = details.host if details.port == 22 else f"[{details.host}]:{details.port}"
    trusted = []
    filenames = {manager.base.ssh_known_hosts_file, manager.settings().ssh_known_hosts_file}
    for filename in filenames:
        if filename and filename.is_file():
            trusted.extend(parse_keys(await bounded_output("ssh-keygen", "-F", target, "-f", str(filename))))
    trust = payload.get("trust_fingerprint", "")
    if trust:
        accepted = next((key for key in scanned if key.fingerprint == trust), None)
        if not accepted:
            return details, None, {"ok":False, "message":"设备指纹已变化，请重新测试连接并核对指纹"}
    else:
        accepted = next((key for key in scanned if key.fingerprint in {item.fingerprint for item in trusted}), None)
    if accepted:
        return details, accepted, None
    key = scanned[0]
    return details, None, {"ok":False, "needs_trust":True, "host_key":key.public(),
                           "message":"设备主机密钥已变化，请核对后重新信任" if trusted else "首次连接此设备，请核对并确认 SSH 指纹"}


async def test_connection(manager: ConnectionManager, details: ConnectionDetails, key: HostKey) -> dict:
    with manager.temporary_settings(details, key.line(details)) as settings:
        capture = SSHCapture(settings)
        events = capture.events(script=PROBE_SCRIPT)
        try:
            async with asyncio.timeout(15):
                async for event in events:
                    if event["type"] == "ready":
                        return {"ok":True, "message":"SSH 登录及采集权限测试通过"}
            return {"ok":False, "message":"设备未确认采集就绪"}
        except (CaptureError, OSError, TimeoutError):
            return {"ok":False, "message":"SSH 登录或采集权限测试失败，请检查用户、密码及免密 sudo 权限"}
        finally:
            await events.aclose()
            await capture.close()
