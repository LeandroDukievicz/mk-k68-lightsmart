"""Physical key layout of the 68-key board.

Transcribed from ``KeyboardLayout.xml`` as shipped with the Windows
driver, so the ``light_index`` values match what the firmware expects.
The geometry is the vendor editor's 800x300 artwork canvas, kept so the
GUI can lay the board out the same way.

The vendor file labels both shifts "Shift" and both alts "Alt", so keys
also carry a unique ``ident`` -- looking them up by the printed label
alone would quietly lose one of each pair.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    """One physical key.

    ``code`` is the HID usage the key sends by default; ``light_index``
    addresses its LED. ``name`` is what is printed on the keycap and is
    not unique; ``ident`` is.
    """

    ident: str
    name: str
    code: int
    light_index: int
    x: int
    y: int
    w: int
    h: int


CANVAS_SIZE = (800, 300)

#: Smallest ``y`` any key occupies -- the dot-matrix strip sits above it.
KEYS_TOP = 33

KEYS: tuple[Key, ...] = (
    Key('SWITCH', 'SWITCH', 0x9d, 124, 749, 33, 18, 36),
    Key('Esc', 'Esc', 0x29, 1, 40, 88, 45, 38),
    Key('1', '1', 0x1e, 20, 86, 88, 45, 38),
    Key('2', '2', 0x1f, 21, 133, 88, 45, 38),
    Key('3', '3', 0x20, 22, 179, 88, 45, 38),
    Key('4', '4', 0x21, 23, 225, 88, 45, 38),
    Key('5', '5', 0x22, 24, 272, 88, 45, 38),
    Key('6', '6', 0x23, 25, 318, 88, 45, 38),
    Key('7', '7', 0x24, 26, 365, 88, 45, 38),
    Key('8', '8', 0x25, 27, 411, 88, 45, 38),
    Key('9', '9', 0x26, 28, 458, 88, 45, 38),
    Key('0', '0', 0x27, 29, 504, 88, 45, 38),
    Key('-', '-', 0x2d, 30, 550, 88, 45, 38),
    Key('=', '=', 0x2e, 31, 596, 88, 45, 38),
    Key('Backspace', 'Backspace', 0x2a, 103, 642, 88, 92, 38),
    Key('~', '~', 0x35, 19, 735, 88, 45, 38),
    Key('Tab', 'Tab', 0x2b, 37, 40, 127, 67, 38),
    Key('Q', 'Q', 0x14, 38, 108, 127, 45, 38),
    Key('W', 'W', 0x1a, 39, 155, 127, 45, 38),
    Key('E', 'E', 0x08, 40, 201, 127, 45, 38),
    Key('R', 'R', 0x15, 41, 247, 127, 45, 38),
    Key('T', 'T', 0x17, 42, 293, 127, 45, 38),
    Key('Y', 'Y', 0x1c, 43, 340, 127, 45, 38),
    Key('U', 'U', 0x18, 44, 386, 127, 45, 38),
    Key('I', 'I', 0x0c, 45, 432, 127, 45, 38),
    Key('O', 'O', 0x12, 46, 478, 127, 45, 38),
    Key('P', 'P', 0x13, 47, 525, 127, 45, 38),
    Key('[', '[', 0x2f, 48, 571, 127, 45, 38),
    Key(']', ']', 0x30, 49, 617, 127, 45, 38),
    Key('\\|', '\\|', 0x31, 67, 663, 127, 71, 38),
    Key('Del', 'Del', 0x4c, 119, 735, 127, 45, 38),
    Key('Caps', 'Caps', 0x39, 55, 40, 166, 79, 38),
    Key('A', 'A', 0x04, 56, 120, 166, 45, 38),
    Key('S', 'S', 0x16, 57, 166, 166, 45, 38),
    Key('D', 'D', 0x07, 58, 212, 166, 45, 38),
    Key('F', 'F', 0x09, 59, 259, 166, 45, 38),
    Key('G', 'G', 0x0a, 60, 305, 166, 45, 38),
    Key('H', 'H', 0x0b, 61, 351, 166, 45, 38),
    Key('J', 'J', 0x0d, 62, 398, 166, 45, 38),
    Key('K', 'K', 0x0e, 63, 444, 166, 45, 38),
    Key('L', 'L', 0x0f, 64, 490, 166, 45, 38),
    Key(';', ';', 0x33, 65, 537, 166, 45, 38),
    Key('"', '"', 0x34, 66, 583, 166, 45, 38),
    Key('Enter', 'Enter', 0x28, 85, 630, 166, 104, 38),
    Key('PgUp', 'PgUp', 0x4b, 118, 735, 166, 45, 38),
    Key('LeftShift', 'Shift', 0xe1, 73, 40, 205, 102, 38),
    Key('Z', 'Z', 0x1d, 74, 143, 205, 45, 38),
    Key('X', 'X', 0x1b, 75, 189, 205, 45, 38),
    Key('C', 'C', 0x06, 76, 235, 205, 45, 38),
    Key('V', 'V', 0x19, 77, 282, 205, 45, 38),
    Key('B', 'B', 0x05, 78, 328, 205, 45, 38),
    Key('N', 'N', 0x11, 79, 375, 205, 45, 38),
    Key('M', 'M', 0x10, 80, 421, 205, 45, 38),
    Key('<', '<', 0x36, 81, 468, 205, 45, 38),
    Key('>', '>', 0x37, 82, 514, 205, 45, 38),
    Key('?', '?', 0x38, 83, 561, 205, 45, 38),
    Key('RightShift', 'Shift', 0xe5, 84, 608, 205, 79, 38),
    Key('↑', '↑', 0x52, 101, 689, 205, 45, 38),
    Key('PgDn', 'PgDn', 0x4e, 121, 735, 205, 45, 38),
    Key('Control', 'Control', 0xe0, 91, 40, 244, 56, 38),
    Key('Win', 'Win', 0xe3, 92, 97, 244, 56, 38),
    Key('LeftAlt', 'Alt', 0xe2, 93, 155, 244, 56, 38),
    Key('Space', 'Space', 0x2c, 94, 212, 244, 290, 38),
    Key('RightAlt', 'Alt', 0xe6, 95, 503, 244, 45, 38),
    Key('Fn', 'Fn', 0xaf, 96, 549, 244, 45, 38),
    Key('RightCtrl', 'RightCtrl', 0xe4, 98, 595, 244, 45, 38),
    Key('←', '←', 0x50, 99, 642, 244, 45, 38),
    Key('↓', '↓', 0x51, 100, 689, 244, 45, 38),
    Key('→', '→', 0x4f, 102, 735, 244, 45, 38),
)

BY_IDENT: dict[str, Key] = {k.ident: k for k in KEYS}
BY_LIGHT_INDEX: dict[int, Key] = {k.light_index: k for k in KEYS}

#: Printed labels that match more than one key, and what they expand to.
AMBIGUOUS: dict[str, tuple[str, ...]] = {
    'Alt': ('LeftAlt', 'RightAlt'),
    'Shift': ('LeftShift', 'RightShift'),
}
