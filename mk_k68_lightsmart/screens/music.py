"""The music screen: the host listens, the keyboard is told colours."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import layout as kb_layout
from .. import protocol
from ..gui import ACCENT, MUTED, Card, KeyboardView, caption, ghost_button, primary_button
from .common import _card, _combo, _heading, _list, _note


# ----------------------------------------------------------------------
# 5. Music
# ----------------------------------------------------------------------
class LevelBar(QWidget):
    """A meter, so the screen shows what it would be driving."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(54)
        self._phase = 0.0
        # A frozen frame rather than a flat line: at rest the screen
        # should still show the shape of the thing it would drive.
        self.levels = [0.0] * 24
        self._tick()
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def run(self, on: bool) -> None:
        self._timer.start(50) if on else self._timer.stop()
        if not on:
            self.levels = [0.0] * len(self.levels)
            self.update()

    def _tick(self) -> None:
        import math
        self._phase += 0.25
        self.levels = [
            abs(math.sin(self._phase + i * 0.4)) * (0.35 + 0.65 * (i % 5) / 4)
            for i in range(len(self.levels))
        ]
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#061634"))
        n = len(self.levels)
        w = self.width() / n
        for i, level in enumerate(self.levels):
            h = max(2.0, level * (self.height() - 6))
            colour = QColor("#3ddc84") if level < 0.6 else (
                QColor("#ffc46b") if level < 0.85 else QColor("#ff6b6b"))
            p.fillRect(int(i * w + 2), int(self.height() - h - 3),
                       int(w - 3), int(h), colour)


class MusicPage(QWidget):
    """What the keyboard would do with sound, and who would do it.

    The listening is the host's: the keyboard is told colours, the same
    way every other lighting path tells it colours. So this needs an
    audio capture, not a new command -- which is why the controls are
    real and the meter is a stand-in.

    The controls are the vendor's: a colour scheme for the bars, how
    much of the range to use, and what the unlit keys do behind them.
    """

    status = Signal(str)

    SCHEMES = ("Green / yellow / red", "Single colour", "Spectrum")
    BACKGROUNDS = ("Nothing", "Dim static", "The current effect")

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 14)
        outer.setSpacing(12)
        outer.addWidget(_heading("Music mode"))
        outer.addWidget(_note(
            "No new command is needed for this — the host listens and "
            "sends colours, through the same per-key path the Home "
            "screen uses. What is missing is the audio capture, so the "
            "meter below is a stand-in and nothing reaches the keyboard."
        ))

        self.meter = LevelBar()

        self.scheme = _combo(list(self.SCHEMES))
        self.background = _combo(list(self.BACKGROUNDS))

        self.amplitude = QSlider(Qt.Horizontal)
        self.amplitude.setRange(1, 100)
        self.amplitude.setValue(60)
        self.back_bright = QSlider(Qt.Horizontal)
        self.back_bright.setRange(0, 100)
        self.back_bright.setValue(20)

        self.preview = QRadioButton("Animate the meter")
        self.preview.setStyleSheet("color:#9fb3d0; font-size:11px;")
        self.preview.toggled.connect(self.meter.run)
        group = QButtonGroup(self)
        group.setExclusive(False)
        group.addButton(self.preview)

        row = QHBoxLayout()
        row.addWidget(_card(
            caption("Visualisation"), self.scheme,
            caption("Amplitude"), self.amplitude,
            caption("Behind it"), self.background,
            caption("Background brightness"), self.back_bright,
            self.preview,
        ), 1)
        outer.addWidget(_card(caption("Levels"), self.meter))
        outer.addLayout(row)
        outer.addStretch()
