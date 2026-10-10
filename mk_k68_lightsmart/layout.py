"""Physical key layout of the 68-key board.

Transcribed from ``KeyboardLayout.xml`` as shipped with the Windows
driver, so the ``light_index`` values match what the firmware expects.
The geometry is the vendor editor's 800x300 artwork canvas, kept so the
GUI can lay the board out the same way.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    """One physical key.

    ``code`` is the HID usage the key sends by default; ``light_index``
    addresses its LED in the per-key colour command.
    """

    name: str
    code: int
    key_index: int
    light_index: int
    x: int
    y: int
    w: int
    h: int


CANVAS_SIZE = (800, 300)

KEYS: tuple[Key, ...] = (
    Key('SWITCH', 0x9d, 124, 124, 749, 33, 18, 36),
    Key('Esc', 0x29, 1, 1, 40, 88, 45, 38),
    Key('1', 0x1e, 20, 20, 86, 88, 45, 38),
    Key('2', 0x1f, 21, 21, 133, 88, 45, 38),
    Key('3', 0x20, 22, 22, 179, 88, 45, 38),
    Key('4', 0x21, 23, 23, 225, 88, 45, 38),
    Key('5', 0x22, 24, 24, 272, 88, 45, 38),
    Key('6', 0x23, 25, 25, 318, 88, 45, 38),
    Key('7', 0x24, 26, 26, 365, 88, 45, 38),
    Key('8', 0x25, 27, 27, 411, 88, 45, 38),
    Key('9', 0x26, 28, 28, 458, 88, 45, 38),
    Key('0', 0x27, 29, 29, 504, 88, 45, 38),
    Key('-', 0x2d, 30, 30, 550, 88, 45, 38),
    Key('=', 0x2e, 31, 31, 596, 88, 45, 38),
    Key('Backspace', 0x2a, 103, 103, 642, 88, 92, 38),
    Key('~', 0x35, 19, 19, 735, 88, 45, 38),
    Key('Tab', 0x2b, 37, 37, 40, 127, 67, 38),
    Key('Q', 0x14, 38, 38, 108, 127, 45, 38),
    Key('W', 0x1a, 39, 39, 155, 127, 45, 38),
    Key('E', 0x08, 40, 40, 201, 127, 45, 38),
    Key('R', 0x15, 41, 41, 247, 127, 45, 38),
    Key('T', 0x17, 42, 42, 293, 127, 45, 38),
    Key('Y', 0x1c, 43, 43, 340, 127, 45, 38),
    Key('U', 0x18, 44, 44, 386, 127, 45, 38),
    Key('I', 0x0c, 45, 45, 432, 127, 45, 38),
    Key('O', 0x12, 46, 46, 478, 127, 45, 38),
    Key('P', 0x13, 47, 47, 525, 127, 45, 38),
    Key('[', 0x2f, 48, 48, 571, 127, 45, 38),
    Key(']', 0x30, 49, 49, 617, 127, 45, 38),
    Key('\\|', 0x31, 67, 67, 663, 127, 71, 38),
    Key('Del', 0x4c, 119, 119, 735, 127, 45, 38),
    Key('Caps', 0x39, 55, 55, 40, 166, 79, 38),
    Key('A', 0x04, 56, 56, 120, 166, 45, 38),
    Key('S', 0x16, 57, 57, 166, 166, 45, 38),
    Key('D', 0x07, 58, 58, 212, 166, 45, 38),
    Key('F', 0x09, 59, 59, 259, 166, 45, 38),
    Key('G', 0x0a, 60, 60, 305, 166, 45, 38),
    Key('H', 0x0b, 61, 61, 351, 166, 45, 38),
    Key('J', 0x0d, 62, 62, 398, 166, 45, 38),
    Key('K', 0x0e, 63, 63, 444, 166, 45, 38),
    Key('L', 0x0f, 64, 64, 490, 166, 45, 38),
    Key(';', 0x33, 65, 65, 537, 166, 45, 38),
    Key('"', 0x34, 66, 66, 583, 166, 45, 38),
    Key('Enter', 0x28, 85, 85, 630, 166, 104, 38),
    Key('PgUp', 0x4b, 118, 118, 735, 166, 45, 38),
    Key('Shift', 0xe1, 73, 73, 40, 205, 102, 38),
    Key('Z', 0x1d, 74, 74, 143, 205, 45, 38),
    Key('X', 0x1b, 75, 75, 189, 205, 45, 38),
    Key('C', 0x06, 76, 76, 235, 205, 45, 38),
    Key('V', 0x19, 77, 77, 282, 205, 45, 38),
    Key('B', 0x05, 78, 78, 328, 205, 45, 38),
    Key('N', 0x11, 79, 79, 375, 205, 45, 38),
    Key('M', 0x10, 80, 80, 421, 205, 45, 38),
    Key('<', 0x36, 81, 81, 468, 205, 45, 38),
    Key('>', 0x37, 82, 82, 514, 205, 45, 38),
    Key('?', 0x38, 83, 83, 561, 205, 45, 38),
    Key('Shift', 0xe5, 84, 84, 608, 205, 79, 38),
    Key('↑', 0x52, 101, 101, 689, 205, 45, 38),
    Key('PgDn', 0x4e, 121, 121, 735, 205, 45, 38),
    Key('Control', 0xe0, 91, 91, 40, 244, 56, 38),
    Key('Win', 0xe3, 92, 92, 97, 244, 56, 38),
    Key('Alt', 0xe2, 93, 93, 155, 244, 56, 38),
    Key('Space', 0x2c, 94, 94, 212, 244, 290, 38),
    Key('Alt', 0xe6, 95, 95, 503, 244, 45, 38),
    Key('Fn', 0xaf, 96, 96, 549, 244, 45, 38),
    Key('RightCtrl', 0xe4, 98, 98, 595, 244, 45, 38),
    Key('←', 0x50, 99, 99, 642, 244, 45, 38),
    Key('↓', 0x51, 100, 100, 689, 244, 45, 38),
    Key('→', 0x4f, 102, 102, 735, 244, 45, 38),
)

BY_NAME: dict[str, Key] = {k.name: k for k in KEYS}
BY_LIGHT_INDEX: dict[int, Key] = {k.light_index: k for k in KEYS}
