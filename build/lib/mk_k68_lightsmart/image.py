"""Pictures into strip frames.

The vendor's own library ships its designs as wide, short GIFs -- 700x70
for the ones on machenike.com, 650x50 in the manual's advice -- which is
the strip drawn at ten or more pixels per LED. Scaling one back down to
65x5 is the whole conversion.

Qt lives here rather than in :mod:`.matrix` so that the encoder, and its
tests, stay free of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader

from .matrix import COLS, MAX_FRAMES_ANIMATION, ROWS, Frame


@dataclass(frozen=True)
class Imported:
    """What a file turned into, and what it was before."""

    frames: list[Frame]
    #: frames the file holds, before any stride or cap was applied
    available: int
    #: the file's own delay between frames, in ms; 0 if it does not say
    delay_ms: int
    size: tuple[int, int]

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1


def frame_from_image(image: QImage) -> Frame:
    """One picture, squashed to the strip's 65x5.

    Smooth scaling, not nearest: a source drawn at ten pixels per LED
    averages each block back to the colour it was, and a photograph
    turns into something recognisable rather than 325 arbitrary samples.
    """
    small = image.convertToFormat(QImage.Format_RGB32).scaled(
        COLS, ROWS, Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )
    frame = Frame()
    pixels = frame.pixels
    for row in range(ROWS):
        base = row * COLS
        for col in range(COLS):
            pixels[base + col] = small.pixel(col, row) & 0xFFFFFF
    return frame


def load(path: str, *, stride: int = 1, limit: int | None = None) -> Imported:
    """Read an image or an animation and return it as strip frames.

    ``stride`` keeps every nth frame: the keyboard takes 15 ms a block,
    so a 312-frame GIF is a seventy-second upload, and every second or
    third frame usually looks the same on 325 LEDs.

    Raises ValueError with the reader's own complaint if the file is not
    an image this build of Qt can read.
    """
    if stride < 1:
        raise ValueError("stride is 1 or more")

    reader = QImageReader(path)
    # Trust the content, not the extension: the library's files are
    # served with names that carry no suffix at all.
    reader.setDecideFormatFromContent(True)
    size = reader.size()
    delay = reader.nextImageDelay()

    frames: list[Frame] = []
    available = 0
    cap = MAX_FRAMES_ANIMATION if limit is None else min(limit, MAX_FRAMES_ANIMATION)
    while True:
        image = reader.read()
        if image.isNull():
            break
        if available % stride == 0 and len(frames) < cap:
            frames.append(frame_from_image(image))
        available += 1
        if not reader.supportsAnimation():
            break

    if not frames:
        raise ValueError(reader.errorString() or f"{path} holds no image")

    return Imported(
        frames=frames,
        available=available,
        delay_ms=max(0, delay),
        size=(size.width(), size.height()),
    )
