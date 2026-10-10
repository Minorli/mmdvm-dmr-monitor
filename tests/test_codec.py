"""Exercise the actual software vocoder, using an upstream DMR silence vector."""

import struct
import unittest
from pathlib import Path

from backend.codec import CodecUnavailable, DMRDecoder

# MMDVMHost DMRDefines.h, DMR_SILENCE_DATA, excluding TAG_DATA and 0x00.
# This published wire vector tests the burst split and AMBE interleaver together.
# https://github.com/g4klx/MMDVMHost/blob/master/DMRDefines.h
SILENCE_BURST = bytes.fromhex(
    "b9 e8 81 52 61 73 00 2a 6b b9 e8 81 52 60 "
    "00 00 00 00 00 01 73 00 2a 6b b9 e8 81 52 61 73 00 2a 6b"
)


class CodecTests(unittest.TestCase):
    def test_standard_silence_is_60ms_with_no_fec_errors(self):
        with DMRDecoder() as decoder:
            pcm = decoder.decode(SILENCE_BURST)
            self.assertEqual(len(pcm), 960)
            self.assertEqual(decoder.last_errors, (0, 0, 0))
            samples = struct.unpack("<480h", pcm)
            # AMBE silence synthesizes very quiet comfort noise, not exact zeros.
            rms = (sum(sample * sample for sample in samples) / 480) ** 0.5
            self.assertLess(rms, 100)

    def test_single_protected_bit_is_corrected(self):
        damaged = bytearray(SILENCE_BURST)
        damaged[0] ^= 0x80
        with DMRDecoder() as clean, DMRDecoder() as repaired:
            self.assertEqual(clean.decode(SILENCE_BURST), repaired.decode(damaged))
            self.assertEqual(repaired.last_errors, (1, 0, 0))

    def test_sync_and_embedded_signalling_do_not_enter_vocoder(self):
        changed = bytearray(SILENCE_BURST)
        changed[13] ^= 0x0F
        changed[14:19] = bytes.fromhex("75 fd 7d f7 5f")
        changed[19] ^= 0xF0
        with DMRDecoder() as original, DMRDecoder() as other_sync:
            self.assertEqual(
                original.decode(SILENCE_BURST), other_sync.decode(changed)
            )
            self.assertEqual(other_sync.last_errors, (0, 0, 0))

    def test_slot_instances_do_not_share_history_or_noise_generator(self):
        with DMRDecoder() as first, DMRDecoder() as second, DMRDecoder() as busy:
            for _ in range(5):
                expected = first.decode(SILENCE_BURST)
                busy.decode(bytes(33))
                self.assertEqual(expected, second.decode(SILENCE_BURST))

    def test_reset_restarts_history(self):
        with DMRDecoder() as decoder:
            first_pcm = decoder.decode(SILENCE_BURST)
            decoder.decode(bytes(33))
            decoder.reset()
            self.assertEqual(decoder.last_errors, (0, 0, 0))
            self.assertEqual(first_pcm, decoder.decode(SILENCE_BURST))

    def test_wrong_length_and_type_are_rejected(self):
        with DMRDecoder() as decoder:
            for length in (0, 9, 27, 32, 34, 55):
                with self.subTest(length=length), self.assertRaises(ValueError):
                    decoder.decode(bytes(length))
            for value in (None, "0" * 33, 33, [0] * 33):
                with self.subTest(value=value), self.assertRaises(TypeError):
                    decoder.decode(value)
            self.assertEqual(decoder.last_errors, (0, 0, 0))

    def test_close_is_idempotent_and_prevents_use(self):
        decoder = DMRDecoder()
        decoder.close()
        decoder.close()
        with self.assertRaises(RuntimeError):
            decoder.decode(SILENCE_BURST)
        with self.assertRaises(RuntimeError):
            decoder.reset()

    def test_unavailable_library_has_actionable_error(self):
        with self.assertRaisesRegex(CodecUnavailable, "build-codec.sh"):
            DMRDecoder(Path(__file__).parent / "missing-dmrdecode.so")


if __name__ == "__main__":
    unittest.main()
