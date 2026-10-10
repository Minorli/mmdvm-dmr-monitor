"""Executed over SSH on Pi-Star; captures copies of DMR UDP packets only."""
import json
import socket
import struct
import time


def main():
    capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    capture.settimeout(1)
    print(json.dumps({"type": "ready"}), flush=True)
    last_heartbeat = time.monotonic()
    while True:
        if time.monotonic() - last_heartbeat >= 5:
            print(json.dumps({"type": "heartbeat", "at": time.time()}), flush=True)
            last_heartbeat = time.monotonic()
        try:
            frame, address = capture.recvfrom(2048)
        except TimeoutError:
            continue
        if len(frame) < 42 or frame[12:14] != b"\x08\x00":
            continue
        ip = frame[14:]
        ihl = (ip[0] & 15) * 4
        if ihl < 20 or ip[9] != 17 or len(ip) < ihl + 8 or int.from_bytes(ip[6:8], "big") & 0x3FFF:
            continue
        src_port, dst_port, length, _ = struct.unpack("!HHHH", ip[ihl:ihl + 8])
        if src_port != 62031 and dst_port != 62031:
            continue
        payload = ip[ihl + 8:ihl + length]
        if len(payload) != 55 or payload[:4] != b"DMRD":
            continue
        print(json.dumps({"type": "packet", "at": time.time(), "direction": "outgoing" if address[2] == 4 else "incoming", "payload": payload.hex()}), flush=True)


if __name__ == "__main__":
    main()
