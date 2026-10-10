# Local DMR software decoder

Run `./scripts/build-codec.sh` from any directory. The script needs Git, a C11
compiler and the C math library. It downloads mbelib into `.cache/mbelib` at
commit `9a04ed5c78176a9965f3d43f7aa1b1f5330e771f`, builds
`native/build/libdmrdecode.so`, and never installs system packages or accesses a
radio. Subsequent builds use the pinned local checkout without network access.

```python
from backend.codec import DMRDecoder

with DMRDecoder() as decoder:
    pcm_s16le = decoder.decode(voice_packet[20:53])
    fec_errors = decoder.last_errors
```

The application must identify voice DMRD packets before decoding. Each 33-byte
burst contains three 72-bit AMBE+2 codewords split around 48 bits of sync or
embedded signalling. Each codeword decodes to 160 samples, so a burst returns
960 bytes: 480 signed 16-bit little-endian mono samples at 8000 Hz, or 60 ms.
Use a separate instance per active slot/call and `reset()` at a new call.
`close()` is idempotent, and an unavailable native library raises
`CodecUnavailable` with a build instruction. Python serializes calls on an
instance; the C interface expects its caller to do that.

The adapter deinterleaves AMBE codewords and uses mbelib's Golay error correction,
parameter history and synthesis. `last_errors` reports mbelib's corrected-bit counts for
the three codewords; those counts are diagnostics, not an integrity guarantee
or a detector for encrypted traffic. No MD380 firmware, hardware vocoder or
vocoder service is used. Unvoiced synthesis has an instance-local PRNG, replacing
upstream mbelib's global `rand()` calls during compilation. Silence frames
generate quiet comfort noise rather than guaranteed all-zero samples.

The tests exercise a published MMDVMHost silence burst, its corrected single-bit
variant, arbitrary sync fields, instance independence, reset and input/lifecycle
errors. Run `python3 -m unittest discover -s tests -p test_codec.py -v` after
building, or include the tests in the project's pytest run.

## Sources and licensing

- [mbelib](https://github.com/szechyjs/mbelib/tree/9a04ed5c78176a9965f3d43f7aa1b1f5330e771f)
  uses the ISC permission notice in its `COPYRIGHT` file and C sources. The full
  notice is retained in `THIRD_PARTY_LICENSES.txt` and copied beside the built
  library. Its README separately contains a patent notice: compiled vocoder
  implementations may be covered by patents and upstream advises checking
  relevant patent/licensing requirements. ISC source permission does not grant
  patent rights.
- The AMBE interleaving coordinates follow
  [DroidStar's mbe/mbevocoder.cpp](https://github.com/nostar/DroidStar/blob/c6a4c54e7c142814d44e1a887982391e99354e5c/mbe/mbevocoder.cpp)
  (ISC permission notice in that file). That notice is retained in
  `THIRD_PARTY_LICENSES.txt`. DroidStar's separate `dmr.cpp` is GPL-3.0-or-later;
  it was consulted to verify the wire layout, and its code is not included in
  this decoder. The bit extraction here is a direct implementation of the
  108-bit voice / 48-bit signalling / 108-bit voice layout.
- The test wire vector comes from
  [MMDVMHost DMRDefines.h](https://github.com/g4klx/MMDVMHost/blob/master/DMRDefines.h),
  whose file notice is GPL-2.0-or-later. Only the published silence frame values
  are used as a protocol fixture; MMDVMHost source is not built into this library.

The new adapter files carry an ISC SPDX identifier. The decoder does not support
decrypting encrypted DMR audio. Validation here establishes decoding and FEC of
the published test vector; radio reception and intelligibility of a live call
are separate end-to-end checks.
