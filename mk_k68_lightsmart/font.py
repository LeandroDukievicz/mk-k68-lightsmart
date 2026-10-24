"""A 3x5 font, which is as much type as a five-row strip can hold.

Every glyph is three columns wide with one column of air after it, so
the strip fits sixteen characters. Small enough to read from a keyboard,
big enough to tell a 6 from an 8.
"""

from __future__ import annotations

from .matrix import COLS, ROWS, Frame

GLYPH_WIDTH = 3
GLYPH_HEIGHT = 5
#: Columns a character takes, its trailing gap included.
ADVANCE = GLYPH_WIDTH + 1

_GLYPHS: dict[str, tuple[str, ...]] = {
    " ": ("   ", "   ", "   ", "   ", "   "),
    "A": (" # ", "# #", "###", "# #", "# #"),
    "B": ("## ", "# #", "## ", "# #", "## "),
    "C": (" ##", "#  ", "#  ", "#  ", " ##"),
    "D": ("## ", "# #", "# #", "# #", "## "),
    "E": ("###", "#  ", "## ", "#  ", "###"),
    "F": ("###", "#  ", "## ", "#  ", "#  "),
    "G": (" ##", "#  ", "# #", "# #", " ##"),
    "H": ("# #", "# #", "###", "# #", "# #"),
    "I": ("###", " # ", " # ", " # ", "###"),
    "J": ("  #", "  #", "  #", "# #", " # "),
    "K": ("# #", "# #", "## ", "# #", "# #"),
    "L": ("#  ", "#  ", "#  ", "#  ", "###"),
    "M": ("# #", "###", "###", "# #", "# #"),
    "N": ("## ", "# #", "# #", "# #", "# #"),
    "O": (" # ", "# #", "# #", "# #", " # "),
    "P": ("## ", "# #", "## ", "#  ", "#  "),
    "Q": (" # ", "# #", "# #", "## ", " ##"),
    "R": ("## ", "# #", "## ", "# #", "# #"),
    "S": (" ##", "#  ", " # ", "  #", "## "),
    "T": ("###", " # ", " # ", " # ", " # "),
    "U": ("# #", "# #", "# #", "# #", "###"),
    "V": ("# #", "# #", "# #", "# #", " # "),
    "W": ("# #", "# #", "###", "###", "# #"),
    "X": ("# #", "# #", " # ", "# #", "# #"),
    "Y": ("# #", "# #", " # ", " # ", " # "),
    "Z": ("###", "  #", " # ", "#  ", "###"),
    "0": ("###", "# #", "# #", "# #", "###"),
    "1": (" # ", "## ", " # ", " # ", "###"),
    "2": ("###", "  #", "###", "#  ", "###"),
    "3": ("###", "  #", " ##", "  #", "###"),
    "4": ("# #", "# #", "###", "  #", "  #"),
    "5": ("###", "#  ", "###", "  #", "###"),
    "6": ("###", "#  ", "###", "# #", "###"),
    "7": ("###", "  #", "  #", "  #", "  #"),
    "8": ("###", "# #", "###", "# #", "###"),
    "9": ("###", "# #", "###", "  #", "###"),
    "%": ("# #", "  #", " # ", "#  ", "# #"),
    ".": ("   ", "   ", "   ", "   ", " # "),
    "-": ("   ", "   ", "###", "   ", "   "),
    "/": ("  #", "  #", " # ", "#  ", "#  "),
    ":": ("   ", " # ", "   ", " # ", "   "),
}

#: Anything with no glyph of its own is drawn as this.
FALLBACK = ("###", "# #", "# #", "# #", "###")


def glyph(character: str) -> tuple[str, ...]:
    return _GLYPHS.get(character.upper(), FALLBACK)


def width(text: str) -> int:
    """Columns ``text`` occupies, without the gap after the last glyph."""
    return max(0, len(text) * ADVANCE - 1)


def draw(frame: Frame, text: str, x: int, colour: int, *,
         fill_to: int | None = None, dim: int | None = None) -> int:
    """Draw ``text`` at column ``x``; returns the column after it.

    With ``fill_to`` the word doubles as a gauge: pixels left of that
    column are drawn in ``colour`` and the rest in ``dim``, so the name
    itself fills up rather than needing a bar of its own.
    """
    column = x
    for character in text:
        rows = glyph(character)
        for row in range(GLYPH_HEIGHT):
            if row >= ROWS:
                break
            for offset, mark in enumerate(rows[row]):
                if mark == " ":
                    continue
                at = column + offset
                if not 0 <= at < COLS:
                    continue
                if fill_to is None or at < fill_to:
                    frame[at, row] = colour
                elif dim is not None:
                    frame[at, row] = dim
        column += ADVANCE
    return column - 1
