"""Pure software DMR AMBE+2 decoding through the local mbelib adapter."""

from __future__ import annotations

import ctypes
import struct
import threading
from pathlib import Path
from typing import Self


class CodecUnavailable(RuntimeError):
    """The native codec has not been built or cannot be loaded."""


class DMRDecoder:
    """Keep one instance per DMR slot/call; return 60 ms of 8 kHz mono S16LE.

    The caller supplies the 33-byte voice burst at packet[20:53], after checking
    that the DMRD packet is voice. FEC counts are available in ``last_errors``.
    Do not share a decoder between calls: close it or reset at each new call.
    """

    sample_rate = 8000
    samples_per_burst = 480

    def __init__(self, library_path: str | Path | None = None) -> None:
        path = (
            Path(library_path)
            if library_path is not None
            else Path(__file__).resolve().parent.parent
            / "native"
            / "build"
            / "libdmrdecode.so"
        )
        self._lock = threading.Lock()
        self._handle = None
        self.last_errors = (0, 0, 0)
        try:
            self._library = ctypes.CDLL(str(path.resolve()))
            self._library.dmr_decoder_new.argtypes = []
            self._library.dmr_decoder_new.restype = ctypes.c_void_p
            self._library.dmr_decoder_free.argtypes = [ctypes.c_void_p]
            self._library.dmr_decoder_free.restype = None
            self._library.dmr_decoder_reset.argtypes = [ctypes.c_void_p]
            self._library.dmr_decoder_reset.restype = ctypes.c_int
            self._library.dmr_decoder_decode.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_int16),
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_int),
            ]
            self._library.dmr_decoder_decode.restype = ctypes.c_int
        except (OSError, AttributeError) as error:
            raise CodecUnavailable(
                f"Cannot load DMR codec {path}; run scripts/build-codec.sh: {error}"
            ) from error
        self._handle = self._library.dmr_decoder_new()
        if not self._handle:
            raise CodecUnavailable("Cannot allocate the DMR decoder")

    def decode(self, burst: bytes | bytearray | memoryview) -> bytes:
        if not isinstance(burst, (bytes, bytearray, memoryview)):
            raise TypeError("DMR voice burst must be bytes-like")
        encoded = bytes(burst)
        if len(encoded) != 33:
            raise ValueError("DMR voice burst must contain exactly 33 bytes")
        with self._lock:
            self._require_open()
            source = (ctypes.c_uint8 * 33).from_buffer_copy(encoded)
            samples = (ctypes.c_int16 * self.samples_per_burst)()
            errors = (ctypes.c_int * 3)()
            result = self._library.dmr_decoder_decode(
                self._handle, source, len(encoded), samples, len(samples), errors
            )
            if result != 0:
                raise RuntimeError("Native DMR decoder rejected the voice burst")
            self.last_errors = tuple(errors)
            # Explicit packing also guarantees little-endian PCM on big-endian hosts.
            return struct.pack("<480h", *samples)

    def reset(self) -> None:
        with self._lock:
            self._require_open()
            if self._library.dmr_decoder_reset(self._handle) != 0:
                raise RuntimeError("Cannot reset the DMR decoder")
            self.last_errors = (0, 0, 0)

    def close(self) -> None:
        with self._lock:
            if self._handle:
                self._library.dmr_decoder_free(self._handle)
                self._handle = None

    def _require_open(self) -> None:
        if not self._handle:
            raise RuntimeError("DMR decoder is closed")

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def __del__(self) -> None:
        # Explicit close/context-manager use is preferred; handle failed __init__.
        if getattr(self, "_handle", None):
            self.close()
