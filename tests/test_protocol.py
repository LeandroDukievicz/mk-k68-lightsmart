"""Checks that the encoders still produce what the Windows driver produces.

The expected bytes here were read out of the driver's disassembly, not
out of this implementation, so these tests fail if a refactor drifts
away from the hardware's actual protocol.
"""

from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mk_k68_lightsmart import layout, matrix, protocol, transport


def hexs(data: bytes, n: int = 16) -> str:
    return " ".join(f"{b:02X}" for b in data[:n])


class ControlFraming(unittest.TestCase):
    def test_begin_matches_driver(self):
        # 0x416F6D: mov word [buf], 0x1804
        self.assertEqual(hexs(protocol.control(protocol.SUB_BEGIN), 4), "04 18 00 00")

    def test_select_lighting_matches_driver(self):
        # 0x4305DF: word 0x1304, then byte[8] = 1
        got = protocol.control(protocol.SUB_SELECT_LIGHT, b8=0x01)
        self.assertEqual(hexs(got, 10), "04 13 00 00 00 00 00 00 01 00")

    def test_keymap_block_matches_driver(self):
        # 0x416F91: word 0x1104, then byte[8] = 9
        got = protocol.control(protocol.SUB_KEYMAP, b8=0x09)
        self.assertEqual(hexs(got, 10), "04 11 00 00 00 00 00 00 09 00")

    def test_payload_is_one_report(self):
        self.assertEqual(len(protocol.control(protocol.SUB_END)), transport.REPORT_SIZE)

    def test_offset_must_be_in_range(self):
        for bad in (64, 65, 0, 1):
            with self.assertRaises(ValueError, msg=f"b{bad}"):
                protocol.control(protocol.SUB_BEGIN, **{f"b{bad}": 1})

    def test_malformed_keyword_rejected(self):
        # "b-1" would otherwise index from the end and silently write
        # byte 63; "b１" is a non-ASCII digit that int() still accepts.
        for bad in ("b-1", "b", "bx", "b\uff11"):
            with self.assertRaises(TypeError, msg=bad):
                protocol.control(protocol.SUB_BEGIN, **{bad: 1})

    def test_value_must_be_a_byte(self):
        for bad in (-1, 256):
            with self.assertRaises(ValueError):
                protocol.control(protocol.SUB_BEGIN, b8=bad)


class DataFrames(unittest.TestCase):
    def test_single_key_matches_driver(self):
        # 0x4309D0 builds: 80 R G B 00*4 idx p9 0F 0F 00 00 AA 55
        got = protocol.data_frame(protocol.MODE_SINGLE_KEY, (0xFF, 0x00, 0x00),
                                  p8=1, p10=0x0F, p11=0x0F)
        self.assertEqual(hexs(got), "80 FF 00 00 00 00 00 00 01 00 0F 0F 00 00 AA 55")

    def test_magic_terminates_the_frame(self):
        got = protocol.data_frame(0x00)
        self.assertEqual((got[14], got[15]), protocol.FRAME_MAGIC)


class StripGeometry(unittest.TestCase):
    def test_dimensions(self):
        self.assertEqual((matrix.COLS, matrix.ROWS), (65, 5))
        self.assertEqual(matrix.LEDS, 325)
        self.assertEqual(matrix.FRAME_BYTES, 975)

    def test_row_first_addressing(self):
        self.assertEqual(matrix.led_index(0, 0), 0)
        self.assertEqual(matrix.led_index(64, 0), 64)
        self.assertEqual(matrix.led_index(0, 1), 65)
        self.assertEqual(matrix.led_index(64, 4), 324)

    def test_roundtrip(self):
        for index in (0, 1, 64, 65, 200, 324):
            self.assertEqual(matrix.led_index(*matrix.led_position(index)), index)

    def test_out_of_range_rejected(self):
        with self.assertRaises(ValueError):
            matrix.led_index(65, 0)
        with self.assertRaises(ValueError):
            matrix.led_index(0, 5)
        with self.assertRaises(ValueError):
            matrix.led_position(325)


class FrameEncoding(unittest.TestCase):
    def test_pixels_go_out_bgr(self):
        f = matrix.Frame()
        f[(0, 0)] = 0xFF8000           # R=FF G=80 B=00
        self.assertEqual(f.encode(255)[:3], bytes((0x00, 0x80, 0xFF)))

    def test_frame_is_975_bytes(self):
        self.assertEqual(len(matrix.Frame().encode()), 975)

    def test_brightness_scales_every_channel(self):
        f = matrix.Frame()
        f[(0, 0)] = 0xFFFFFF
        self.assertEqual(f.encode(128)[:3], bytes((128, 128, 128)))
        self.assertEqual(f.encode(0)[:3], bytes((0, 0, 0)))

    def test_wrong_pixel_count_rejected(self):
        with self.assertRaises(ValueError):
            matrix.Frame([0] * 10)


class AnimationPayload(unittest.TestCase):
    def _frames(self, n: int) -> list[matrix.Frame]:
        return [matrix.Frame() for _ in range(n)]

    def test_boot_header_is_count_then_speed(self):
        a = matrix.Animation(frames=self._frames(3), speed=50, boot=True)
        header = a.payload()[:4]
        count, speed = struct.unpack("<HH", header)
        self.assertEqual(count, 3)
        self.assertEqual(speed, matrix.SPEED_BASE - 50)

    def test_animation_header_is_count(self):
        a = matrix.Animation(frames=self._frames(3))
        self.assertEqual(struct.unpack("<H", a.payload()[:2])[0], 3)

    def test_boot_block_count_matches_driver_arithmetic(self):
        # 134 frames: 134*975 + 4 = 130654; 130654>>6 = 2041; +1 = 2042
        a = matrix.Animation(frames=self._frames(134), boot=True)
        self.assertEqual(a.block_count(), 2042)
        self.assertEqual(len(a.payload()), 2042 * 64)

    def test_boot_clamps_to_134(self):
        a = matrix.Animation(frames=self._frames(200), boot=True)
        self.assertEqual(struct.unpack("<H", a.payload()[:2])[0], 134)

    def test_magic_follows_the_data(self):
        # The driver leaves one spare byte and writes the magic at
        # end+1 (0x429EF5).
        for boot, header in ((True, 4), (False, 2)):
            for n in (1, 3, 64, 134):
                a = matrix.Animation(frames=self._frames(n), boot=boot)
                end = header + n * 975
                p = a.payload()
                self.assertEqual(p[end + 1:end + 3], b"\xaa\x55",
                                 f"boot={boot} n={n}")

    def test_magic_is_always_inside_the_sent_payload(self):
        # Regression: a bytearray *slice* assignment grows the buffer when
        # only one byte is free, which made the payload a non-multiple of
        # 64 and truncated the terminator. 51 and 21 are two such counts.
        for boot, header in ((True, 4), (False, 2)):
            limit = matrix.MAX_FRAMES_BOOT if boot else 400
            for n in range(1, limit + 1):
                a = matrix.Animation(frames=self._frames(1) * n, boot=boot)
                p = a.payload()
                end = header + min(n, a.max_frames) * 975
                self.assertEqual(len(p) % 64, 0, f"boot={boot} n={n}")
                self.assertLess(end + 2, len(p),
                                f"magic outside payload: boot={boot} n={n}")
                self.assertIn(b"\xaa\x55", p, f"boot={boot} n={n}")

    def test_block_count_matches_the_payload(self):
        for boot in (True, False):
            for n in (1, 21, 51, 63, 64, 85, 134):
                a = matrix.Animation(frames=self._frames(1) * n, boot=boot)
                self.assertEqual(a.block_count(), len(a.payload()) // 64,
                                 f"boot={boot} n={n}")

    def test_payload_is_whole_blocks(self):
        for n in (1, 2, 7, 64, 134):
            a = matrix.Animation(frames=self._frames(n), boot=True)
            self.assertEqual(len(a.payload()) % 64, 0)

    def test_empty_animation_rejected(self):
        with self.assertRaises(ValueError):
            matrix.Animation(frames=[]).payload()

    def test_cache_invalidates(self):
        a = matrix.Animation(frames=self._frames(2))
        first = a.payload()
        self.assertIs(a.payload(), first)
        a.frames.append(matrix.Frame())
        a.invalidate()
        self.assertEqual(struct.unpack("<H", a.payload()[:2])[0], 3)


class Conversions(unittest.TestCase):
    def test_brightness_clamps(self):
        self.assertEqual(matrix.brightness_to_byte(0), 0)
        self.assertEqual(matrix.brightness_to_byte(100), 255)
        self.assertEqual(matrix.brightness_to_byte(500), 255)
        self.assertEqual(matrix.brightness_to_byte(-5), 0)

    def test_speed_default_and_clamp(self):
        self.assertEqual(matrix.speed_to_byte(None), matrix.DEFAULT_SPEED)
        self.assertEqual(matrix.speed_to_byte(0), matrix.SPEED_BASE)
        self.assertEqual(matrix.speed_to_byte(300), matrix.SPEED_BASE - 100)


class KeyTable(unittest.TestCase):
    def test_every_key_has_a_unique_led(self):
        indices = [k.light_index for k in layout.KEYS]
        self.assertEqual(len(indices), len(set(indices)))

    def test_key_count(self):
        self.assertEqual(len(layout.KEYS), 69)

    def test_lookup_tables_cover_every_key(self):
        self.assertEqual(len(layout.BY_LIGHT_INDEX), len(layout.KEYS))

    def test_duplicate_names_are_disambiguated(self):
        # Shift, Alt and Control appear twice on the board; a plain
        # name->key dict would silently drop one of each.
        names = [k.name for k in layout.KEYS]
        for dup in ("Shift", "Alt"):
            self.assertGreater(names.count(dup), 1)
        self.assertEqual(len(layout.BY_NAME), len(set(names)))


class IoctlEncoding(unittest.TestCase):
    def test_request_codes(self):
        # _IOC(dir=3, 'H', nr, size) == (3<<30)|(size<<16)|(ord('H')<<8)|nr
        size = transport.WIRE_SIZE
        expected_set = (3 << 30) | (size << 16) | (ord("H") << 8) | 0x06
        expected_get = (3 << 30) | (size << 16) | (ord("H") << 8) | 0x07
        self.assertEqual(transport._hidiocsfeature(size), expected_set)
        self.assertEqual(transport._hidiocgfeature(size), expected_get)

    def test_wire_size_carries_the_report_id_byte(self):
        self.assertEqual(transport.WIRE_SIZE, transport.REPORT_SIZE + 1)


class ReportDescriptorParsing(unittest.TestCase):
    VENDOR = bytes.fromhex("0613ff0901a1011500 26ff00 7508 9540"
                           .replace(" ", "") + "0902810209039102 0a00ffb102 c0"
                           .replace(" ", ""))

    def test_finds_the_vendor_collection(self):
        self.assertEqual(transport._parse_report_descriptor(self.VENDOR),
                         (0xFF13, 0x0001))

    def test_truncated_descriptor_does_not_crash(self):
        for cut in range(len(self.VENDOR)):
            transport._parse_report_descriptor(self.VENDOR[:cut])

    def test_garbage_does_not_hang(self):
        for blob in (b"", b"\x00", b"\xff" * 32, bytes(range(256))):
            transport._parse_report_descriptor(blob)


if __name__ == "__main__":
    unittest.main(verbosity=2)
