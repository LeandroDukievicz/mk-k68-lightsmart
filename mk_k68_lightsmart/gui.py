"""Desktop front end.

Lays out the same screens as the vendor's Windows app -- same sidebar,
same 1268x563 proportions, same palette, which was sampled straight off
the manual's screenshots. The artwork is drawn rather than copied, so
nothing of the manufacturer's ships here.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import (
    QPointF,
    QProcess,
    QRectF,
    QStandardPaths,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import (
    QApplication,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import image
from . import layout as kb_layout
from . import matrix, patterns, protocol, transport
from .worker import DeviceThread

# -- palette, sampled from the vendor screenshots -----------------------
BG = QColor("#050b24")
SIDEBAR = QColor("#062750")
ACCENT = QColor("#10a9fd")
PANEL = QColor("#05122e")
WORDMARK = QColor("#0c74b5")
MUTED = QColor("#6a7d96")
KEYCAP = QColor("#13203d")
KEYCAP_EDGE = QColor("#243a63")

MATRIX_COLS = 65
MATRIX_ROWS = 5


class DotMatrixStrip(QWidget):
    """The 65x5 LED strip that sits above the function row."""

    pixel_clicked = Signal(int, int)

    def __init__(self, cell: int = 15, parent: QWidget | None = None):
        super().__init__(parent)
        self.cell = cell
        self.pixels: dict[tuple[int, int], QColor] = {}
        self.setFixedSize(MATRIX_COLS * cell + 8, MATRIX_ROWS * cell + 8)
        self.setCursor(Qt.CrossCursor)

        # 325 cells get repainted on every stroke, so the rects and the
        # unlit pens are built once rather than per frame.
        self._cells = [
            (col, row, QRectF(4 + col * cell, 4 + row * cell, cell - 2, cell - 2))
            for row in range(MATRIX_ROWS)
            for col in range(MATRIX_COLS)
        ]
        self._off_brush = QBrush(QColor("#0b0f1c"))
        self._off_pen = QPen(QColor("#141a2c"), 1)
        self._bg = QColor("#02060f")

    def clear(self) -> None:
        self.pixels.clear()
        self.update()

    def load_frame(self, frame) -> None:
        """Show a :class:`matrix.Frame`. Dark LEDs stay out of the dict."""
        self.pixels = {
            (index % MATRIX_COLS, index // MATRIX_COLS): QColor(
                (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
            )
            for index, rgb in enumerate(frame.pixels)
            if rgb
        }
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), self._bg)
        lit = self.pixels
        for col, row, r in self._cells:
            colour = lit.get((col, row))
            if colour is None:
                p.setBrush(self._off_brush)
                p.setPen(self._off_pen)
            else:
                p.setBrush(colour)
                p.setPen(QPen(colour.lighter(140), 1))
            p.drawRoundedRect(r, 2, 2)

    def mousePressEvent(self, event) -> None:
        col = int((event.position().x() - 4) // self.cell)
        row = int((event.position().y() - 4) // self.cell)
        if 0 <= col < MATRIX_COLS and 0 <= row < MATRIX_ROWS:
            self.pixel_clicked.emit(col, row)

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.LeftButton:
            self.mousePressEvent(event)


class KeyboardView(QWidget):
    """The 69 keys, drawn at the positions the vendor layout gives."""

    key_clicked = Signal(object)

    def __init__(self, scale: float = 1.18, parent: QWidget | None = None):
        super().__init__(parent)
        self.colours: dict[int, QColor] = {}
        self.selected: kb_layout.Key | None = None
        w, _ = kb_layout.CANVAS_SIZE
        # Crop to what the keys actually occupy, with a small margin. The
        # strip is drawn separately above; a fixed crop used to cut the
        # SWITCH key, which sits higher than the rest of the board.
        top = kb_layout.KEYS_TOP - 8
        bottom = max(k.y + k.h for k in kb_layout.KEYS) + 8
        self.setFixedSize(int(w * scale), int((bottom - top) * scale))
        self.setMouseTracking(True)
        self._hover: kb_layout.Key | None = None

        # Geometry and font never change, so build them once instead of
        # on every repaint and every mouse move.
        self._rects: dict[int, QRectF] = {
            k.light_index: QRectF(k.x * scale, (k.y - top) * scale,
                                  k.w * scale, k.h * scale)
            for k in kb_layout.KEYS
        }
        self._font = QFont()
        self._font.setPointSizeF(7.5)

    def key_at(self, pos: QPointF) -> kb_layout.Key | None:
        for key in kb_layout.KEYS:
            if self._rects[key.light_index].contains(pos):
                return key
        return None

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0a1024"))
        p.setFont(self._font)

        for key in kb_layout.KEYS:
            r = self._rects[key.light_index]
            colour = self.colours.get(key.light_index)
            if colour is not None:
                grad = QLinearGradient(r.topLeft(), r.bottomLeft())
                grad.setColorAt(0.0, colour.lighter(135))
                grad.setColorAt(1.0, colour)
                p.setBrush(QBrush(grad))
                edge = colour.lighter(160)
                label = QColor("#ffffff") if colour.lightness() < 140 else QColor("#10131c")
            else:
                p.setBrush(KEYCAP)
                edge = KEYCAP_EDGE
                label = QColor("#9fb3d0")

            if key is self.selected:
                p.setPen(QPen(ACCENT, 2))
            elif key is self._hover:
                p.setPen(QPen(ACCENT.darker(150), 1.5))
            else:
                p.setPen(QPen(edge, 1))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)

            p.setPen(label)
            p.drawText(r, Qt.AlignCenter, key.name)

    def mouseMoveEvent(self, event) -> None:
        hit = self.key_at(event.position())
        if hit is not self._hover:
            self._hover = hit
            self.setCursor(Qt.PointingHandCursor if hit else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, event) -> None:
        hit = self.key_at(event.position())
        if hit:
            self.selected = hit
            self.update()
            self.key_clicked.emit(hit)


class SidebarButton(QPushButton):
    def __init__(self, glyph: str, caption: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedHeight(50)
        self.setCursor(Qt.PointingHandCursor)
        self.glyph = glyph
        self.caption = caption
        self.setFlat(True)
        # paintEvent reads underMouse(), so the hover state has to trigger
        # a repaint of its own
        self.setAttribute(Qt.WA_Hover, True)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        on = self.isChecked()
        if on:
            p.fillRect(self.rect(), QColor("#0a3568"))
            p.fillRect(0, 0, 3, self.height(), ACCENT)
        elif self.underMouse():
            p.fillRect(self.rect(), QColor("#0a2a53"))

        p.setPen(ACCENT if on else QColor("#8fa5c4"))
        f = QFont()
        f.setPointSizeF(12)
        p.setFont(f)
        p.drawText(QRectF(0, 4, self.width(), 22), Qt.AlignCenter, self.glyph)
        f.setPointSizeF(6.5)
        p.setFont(f)
        p.drawText(QRectF(0, 26, self.width(), 18), Qt.AlignCenter, self.caption)


def caption(text: str) -> QLabel:
    """The small muted label that names the control under it."""
    lb = QLabel(text)
    lb.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
    return lb


def primary_button(text: str, on_click) -> QPushButton:
    """The accented action button, as the vendor UI draws it."""
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        "QPushButton { background:#10a9fd; color:#04121f; border:none;"
        " border-radius:3px; padding:7px 16px; font-weight:bold; font-size:11px; }"
        "QPushButton:hover { background:#3dbdff; }"
        "QPushButton:disabled { background:#123a5c; color:#5b7894; }"
    )
    b.clicked.connect(on_click)
    return b


def ghost_button(text: str, on_click) -> QPushButton:
    """A quieter button, for what sits beside a primary one."""
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        "QPushButton { background:transparent; color:#9fb3d0;"
        " border:1px solid #0d3a6b; border-radius:3px; padding:6px 14px;"
        " font-size:11px; }"
        "QPushButton:hover { color:#cfe3ff; border-color:#10a9fd; }"
        "QPushButton:disabled { color:#44607f; border-color:#0a2748; }"
    )
    b.clicked.connect(on_click)
    return b


class Card(QFrame):
    """A bordered panel in the content area."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        # Scoped by object name: a bare "QFrame { ... }" also matches every
        # child QLabel, which derives from QFrame.
        self.setStyleSheet(
            "QFrame#card { background: rgba(8,28,60,120);"
            " border: 1px solid #0d3a6b; border-radius: 6px; }"
        )


class MatrixPage(QWidget):
    """The strip's own screen: frames, a timeline, playback, upload.

    The Home screen paints one still frame as a quick way to reach the
    strip. This is the editor: a list of frames, each one editable, that
    goes to the keyboard as a single animation.

    It owns no device. Upload is a request the window forwards to the
    worker thread, and progress comes back the same way.
    """

    upload_requested = Signal(object)
    #: stop the upload in flight
    cancel_requested = Signal()
    #: a note for the window's status bar
    status = Signal(str)

    #: The vendor's own speed scale, which the wire value is derived from.
    SPEED_MIN, SPEED_MAX = 1, 100

    #: Frames an import keeps without asking. Past this the upload runs
    #: longer than anyone waits for a lighting effect.
    COMFORTABLE_FRAMES = 120

    #: What the screen is holding when it opens.
    DEFAULT_PATTERN = "tetris"

    def __init__(self, current_colour, parent: QWidget | None = None):
        super().__init__(parent)
        self._colour = current_colour
        # Opens on something worth looking at rather than a blank strip;
        # Clear and Delete empty it for drawing.
        self.frames: list[matrix.Frame] = patterns.build(self.DEFAULT_PATTERN)
        self.current = 0
        self._playing = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(12)

        board = Card()
        bl = QVBoxLayout(board)
        bl.setContentsMargins(18, 14, 18, 14)
        bl.setSpacing(10)

        self.strip = DotMatrixStrip(cell=17)
        self.strip.pixel_clicked.connect(self.on_pixel)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.strip)
        row.addStretch()
        bl.addLayout(row)
        bl.addLayout(self._build_transport())
        lay.addWidget(board)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_timeline())
        body.addWidget(self._build_settings(), 1)
        lay.addLayout(body, 1)

        self._refresh_timeline()

    # -- construction ---------------------------------------------------
    def _build_transport(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()

        self.frame_label = QLabel()
        self.frame_label.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
        row.addWidget(self.frame_label)

        self.play_button = ghost_button("▶  Play", self.toggle_play)
        row.addWidget(self.play_button)

        self.upload_progress = QProgressBar()
        self.upload_progress.setFixedWidth(170)
        self.upload_progress.setFixedHeight(16)
        self.upload_progress.setTextVisible(False)
        self.upload_progress.setVisible(False)
        self.upload_progress.setStyleSheet(
            "QProgressBar { background:#0a2444; border:1px solid #0d3a6b;"
            " border-radius:3px; }"
            "QProgressBar::chunk { background:#10a9fd; border-radius:2px; }"
        )
        row.addWidget(self.upload_progress)

        self.cancel_button = ghost_button("Cancel", self.cancel)
        self.cancel_button.setVisible(False)
        row.addWidget(self.cancel_button)

        self.upload_button = primary_button("Upload animation", self.upload)
        self.upload_button.setEnabled(False)
        row.addWidget(self.upload_button)
        row.addStretch()
        return row

    def _build_timeline(self) -> QWidget:
        card = Card()
        card.setFixedWidth(210)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        title = QLabel("Frames")
        title.setStyleSheet("color:#cfe3ff; font-size:12px; font-weight:bold;")
        lay.addWidget(title)

        self.timeline = QListWidget()
        self.timeline.setStyleSheet(
            "QListWidget { background:#061a33; color:#9fb3d0; border:1px solid"
            " #0d3a6b; border-radius:3px; font-size:11px; }"
            "QListWidget::item { padding:4px 6px; }"
            "QListWidget::item:selected { background:#0a3568; color:#cfe3ff; }"
        )
        self.timeline.currentRowChanged.connect(self.select_frame)
        lay.addWidget(self.timeline, 1)

        for text, slot in (("Add", self.add_frame),
                           ("Duplicate", self.duplicate_frame),
                           ("Delete", self.delete_frame),
                           ("Clear this frame", self.clear_frame)):
            lay.addWidget(ghost_button(text, slot))
        return card

    def _build_settings(self) -> QWidget:
        card = Card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        def caption(text: str) -> QLabel:
            lb = QLabel(text)
            lb.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
            return lb

        title = QLabel("Animation")
        title.setStyleSheet("color:#cfe3ff; font-size:12px; font-weight:bold;")
        lay.addWidget(title)

        speed = QHBoxLayout()
        speed.addWidget(caption("Speed"))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(self.SPEED_MIN, self.SPEED_MAX)
        # Faster than the rate the shipped effects were drawn at, by
        # whatever PLAYBACK_BOOST says -- the same default the rest of
        # the program sends.
        self.speed.setValue(matrix.DEFAULT_PLAYBACK_SPEED)
        self.speed.setFixedWidth(160)
        self.speed.valueChanged.connect(self._speed_changed)
        speed.addWidget(self.speed)
        self.speed_label = caption("")
        speed.addWidget(self.speed_label)
        speed.addStretch()
        lay.addLayout(speed)

        bright = QHBoxLayout()
        bright.addWidget(caption("Brightness"))
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(100)
        self.brightness.setFixedWidth(160)
        bright.addWidget(self.brightness)
        bright.addStretch()
        lay.addLayout(bright)

        built_in = QHBoxLayout()
        built_in.addWidget(caption("Built in"))
        self.built_in = QComboBox()
        for name, label in patterns.catalogue(with_overall=True).items():
            self.built_in.addItem(label, name)
        self.built_in.setStyleSheet(
            "QComboBox { background:#0a2444; color:#cfe3ff; border:1px solid #0d3a6b;"
            " border-radius:3px; padding:4px 8px; font-size:11px; }"
            "QComboBox QAbstractItemView { background:#0a2444; color:#cfe3ff;"
            " selection-background-color:#10a9fd; }"
        )
        built_in.addWidget(self.built_in, 1)
        built_in.addWidget(ghost_button("Load", self.load_built_in))
        lay.addLayout(built_in)

        lay.addWidget(ghost_button("Import an image or GIF…", self.import_image))
        lay.addWidget(ghost_button("Import from a vendor database…",
                                   self.import_vendor))

        note = QLabel(
            "Frames go to the keyboard as one animation, up to "
            f"{matrix.MAX_FRAMES_ANIMATION}. Painting uses the colour "
            "picked on the Home screen."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
        lay.addWidget(note)
        lay.addStretch()
        self._speed_changed(self.speed.value())
        return card

    # -- frames ---------------------------------------------------------
    def _refresh_timeline(self) -> None:
        """Rebuild the list and show the current frame.

        The signal is blocked while the rows are replaced: rebuilding
        emits currentRowChanged, which would select frame 0 behind the
        caller's back.
        """
        blocked = self.timeline.blockSignals(True)
        self.timeline.clear()
        for i in range(len(self.frames)):
            self.timeline.addItem(f"Frame {i + 1}")
        self.timeline.setCurrentRow(self.current)
        self.timeline.blockSignals(blocked)
        self._show_current()

    def _show_current(self) -> None:
        self.strip.load_frame(self.frames[self.current])
        self.frame_label.setText(
            f"frame {self.current + 1} of {len(self.frames)}"
        )

    def select_frame(self, row: int) -> None:
        if 0 <= row < len(self.frames):
            self.current = row
            self._show_current()

    def add_frame(self) -> None:
        if not self._room_for_one_more():
            return
        self.frames.insert(self.current + 1, matrix.Frame())
        self.current += 1
        self._refresh_timeline()

    def duplicate_frame(self) -> None:
        if not self._room_for_one_more():
            return
        copy = matrix.Frame(self.frames[self.current].pixels)
        self.frames.insert(self.current + 1, copy)
        self.current += 1
        self._refresh_timeline()

    def delete_frame(self) -> None:
        if len(self.frames) == 1:
            # An animation needs a frame; clearing is what was meant.
            self.clear_frame()
            return
        del self.frames[self.current]
        self.current = min(self.current, len(self.frames) - 1)
        self._refresh_timeline()

    def clear_frame(self) -> None:
        self.frames[self.current] = matrix.Frame()
        self._show_current()

    def _room_for_one_more(self) -> bool:
        if len(self.frames) < matrix.MAX_FRAMES_ANIMATION:
            return True
        self.status.emit(
            f"the keyboard takes {matrix.MAX_FRAMES_ANIMATION} frames at most"
        )
        return False

    def on_pixel(self, col: int, row: int) -> None:
        colour = self._colour()
        self.frames[self.current][col, row] = colour.rgb() & 0xFFFFFF
        self.strip.pixels[(col, row)] = QColor(colour)
        self.strip.update()

    # -- playback -------------------------------------------------------
    def _speed_changed(self, value: int) -> None:
        self.speed_label.setText(f"{self._interval_ms(value)} ms/frame")
        if self._playing:
            self._timer.setInterval(self._interval_ms(value))

    @staticmethod
    def _interval_ms(speed: int) -> int:
        """Preview pacing, from the same 1..100 the keyboard is sent.

        The firmware's mapping is calibrated now, so the preview uses
        it rather than a guess of its own and the label states what the
        strip will actually do. The floor is the preview's own limit:
        the top of the scale asks for 3 ms a frame, which no timer here
        is going to hit.
        """
        return max(16, round(matrix.frame_ms(speed)))

    def toggle_play(self) -> None:
        if self._playing:
            self.stop()
            return
        if len(self.frames) < 2:
            self.status.emit("one frame is a still picture — add another")
            return
        self._playing = True
        self.play_button.setText("■  Stop")
        self._timer.start(self._interval_ms(self.speed.value()))

    def stop(self) -> None:
        self._playing = False
        self._timer.stop()
        self.play_button.setText("▶  Play")

    def _tick(self) -> None:
        self.current = (self.current + 1) % len(self.frames)
        blocked = self.timeline.blockSignals(True)
        self.timeline.setCurrentRow(self.current)
        self.timeline.blockSignals(blocked)
        self._show_current()

    # -- device ---------------------------------------------------------
    def animation(self) -> matrix.Animation:
        return matrix.Animation(
            frames=list(self.frames),
            speed=self.speed.value(),
            brightness=self.brightness.value(),
        )

    def upload(self) -> None:
        self.stop()
        self.upload_requested.emit(self.animation())

    def cancel(self) -> None:
        self.cancel_button.setEnabled(False)
        self.cancel_requested.emit()

    def set_connected(self, connected: bool) -> None:
        self.upload_button.setEnabled(connected)

    def set_uploading(self, on: bool, connected: bool = True) -> None:
        self.upload_button.setEnabled(connected and not on)
        self.upload_progress.setVisible(on)
        self.cancel_button.setVisible(on)
        self.cancel_button.setEnabled(on)

    def set_progress(self, sent: int, total: int) -> None:
        if self.upload_progress.maximum() != total:
            self.upload_progress.setRange(0, total)
        self.upload_progress.setValue(sent)

    # -- import ---------------------------------------------------------
    def load_built_in(self) -> None:
        """Load one of the animations the program generates itself."""
        name = self.built_in.currentData()
        self.stop()
        self.frames = patterns.build(name)
        self.current = 0
        self._refresh_timeline()
        self.status.emit(
            f"loaded {name} — {len(self.frames)} frames, about "
            f"{self._seconds_for(len(self.frames))} s to upload"
        )

    def import_image(self) -> None:
        """Load a picture or an animation and squash it onto the strip.

        The designs in the vendor's online library are wide, short GIFs
        -- 700x70, hundreds of frames -- so the only real question is
        how many of those frames to keep: the keyboard takes 15 ms a
        block, which makes 312 frames a seventy-second upload.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Open an image or animation", "",
            "Images and animations (*.gif *.png *.jpg *.jpeg *.webp *.bmp);;"
            "All files (*)"
        )
        if not path:
            return
        try:
            peek = image.load(path, limit=1)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Could not read that file", str(exc))
            return

        stride = 1
        if peek.available > self.COMFORTABLE_FRAMES:
            suggested = -(-peek.available // self.COMFORTABLE_FRAMES)
            stride, ok = QInputDialog.getInt(
                self, "Import an animation",
                f"{peek.available} frames, {peek.size[0]}x{peek.size[1]}.\n"
                f"The whole thing uploads in about "
                f"{self._seconds_for(peek.available)} s.\n\n"
                "Keep every nth frame:",
                suggested, 1, peek.available,
            )
            if not ok:
                return

        try:
            loaded = image.load(path, stride=stride)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Could not read that file", str(exc))
            return

        self.stop()
        self.frames = loaded.frames
        self.current = 0
        self._refresh_timeline()
        kept = (f"every {stride} of {loaded.available}" if stride > 1
                else f"all {loaded.available}")
        self.status.emit(
            f"imported {len(self.frames)} frames ({kept}) — "
            f"about {self._seconds_for(len(self.frames))} s to upload"
        )

    @staticmethod
    def _seconds_for(frames: int) -> int:
        """Roughly how long uploading that many frames takes."""
        return round(matrix.Animation._blocks_for(frames * 975 + 4) * 0.015)

    def import_vendor(self) -> None:
        """Load one layer out of a vendor animation database.

        The database is the manufacturer's and is not shipped here; this
        reads a copy the user already has.
        """
        path, _ = QFileDialog.getOpenFileName(
            self, "Open a vendor animation database", "",
            "Animation store (*.data);;All files (*)"
        )
        if not path:
            return
        try:
            animations = matrix.load_vendor_animations(path)
        except Exception as exc:                 # sqlite3 or a bad file
            QMessageBox.warning(self, "Could not read that file", str(exc))
            return
        if not animations:
            QMessageBox.warning(self, "Nothing to import",
                                "That database holds no animations.")
            return

        labels = [
            f"{lid}: {name} ({len(a.frames)} frames)"
            for lid, (name, a) in sorted(animations.items())
        ]
        chosen, ok = QInputDialog.getItem(
            self, "Import an animation", "Layer:", labels, 0, False
        )
        if not ok:
            return
        lid = int(chosen.split(":", 1)[0])
        name, animation = animations[lid]
        self.stop()
        self.frames = animation.frames or [matrix.Frame()]
        self.current = 0
        if animation.speed is not None:
            self.speed.setValue(
                max(self.SPEED_MIN, min(self.SPEED_MAX, animation.speed))
            )
        self.brightness.setValue(max(0, min(100, animation.brightness)))
        self._refresh_timeline()
        self.status.emit(f"imported {name} — {len(self.frames)} frames")


class LightPage(QWidget):
    """The built-in lighting animations, with their parameters.

    The Home screen can fire one off; this is the screen for choosing
    among them, and the only place the unknowns are stated: the mode
    bytes are recovered and confirmed, but which of bytes 8-11 carries
    brightness, speed and direction is still inferred, so the sliders
    say so rather than pretending.
    """

    #: mode, colour, brightness, speed, direction
    apply_requested = Signal(int, int, int, int, int)

    def __init__(self, current_colour, parent: QWidget | None = None):
        super().__init__(parent)
        self._colour = current_colour

        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(12)
        lay.addWidget(self._build_list())
        lay.addWidget(self._build_controls(), 1)

    def _build_list(self) -> QWidget:
        card = Card()
        card.setFixedWidth(210)
        inner = QVBoxLayout(card)
        inner.setContentsMargins(12, 10, 12, 10)
        inner.setSpacing(8)

        title = QLabel("Animations")
        title.setStyleSheet("color:#cfe3ff; font-size:12px; font-weight:bold;")
        inner.addWidget(title)

        self.effects = QListWidget()
        self.effects.setStyleSheet(
            "QListWidget { background:#061a33; color:#9fb3d0; border:1px solid"
            " #0d3a6b; border-radius:3px; font-size:11px; }"
            "QListWidget::item { padding:4px 6px; }"
            "QListWidget::item:selected { background:#0a3568; color:#cfe3ff; }"
        )
        for effect in protocol.EFFECTS:
            self.effects.addItem(f"{effect.mode:#04x}  {effect.name}")
        self.effects.setCurrentRow(0)
        self.effects.itemDoubleClicked.connect(lambda _: self.apply())
        inner.addWidget(self.effects, 1)
        return card

    def _build_controls(self) -> QWidget:
        card = Card()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(16, 12, 16, 12)
        inner.setSpacing(10)

        def caption(text: str) -> QLabel:
            label = QLabel(text)
            label.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
            return label

        title = QLabel("Parameters")
        title.setStyleSheet("color:#cfe3ff; font-size:12px; font-weight:bold;")
        inner.addWidget(title)

        self.sliders: dict[str, QSlider] = {}
        for name, label, default in (("brightness", "Brightness", 10),
                                     ("speed", "Speed", 8),
                                     ("direction", "Direction", 0)):
            row = QHBoxLayout()
            row.addWidget(caption(label))
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 15)
            slider.setValue(default)
            slider.setFixedWidth(170)
            row.addWidget(slider)
            value = caption(str(default))
            slider.valueChanged.connect(
                lambda v, lb=value: lb.setText(str(v)))
            row.addWidget(value)
            row.addStretch()
            inner.addLayout(row)
            self.sliders[name] = slider

        row = QHBoxLayout()
        row.addWidget(primary_button("Apply", self.apply))
        row.addStretch()
        inner.addLayout(row)

        note = QLabel(
            "The mode bytes are the driver's own, confirmed on hardware. "
            "Which of the frame's bytes 8-11 carries brightness, speed and "
            "direction is still inferred — moving a slider may do nothing "
            "until that is settled."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
        inner.addWidget(note)
        inner.addStretch()
        return card

    def selected(self):
        row = self.effects.currentRow()
        if 0 <= row < len(protocol.EFFECTS):
            return protocol.EFFECTS[row]
        return protocol.EFFECTS[0]

    def apply(self) -> None:
        colour = self._colour()
        self.apply_requested.emit(
            self.selected().mode,
            colour.red() << 16 | colour.green() << 8 | colour.blue(),
            self.sliders["brightness"].value(),
            self.sliders["speed"].value(),
            self.sliders["direction"].value(),
        )


class SetupPage(QWidget):
    """Why the keyboard is, or is not, reachable.

    Every install hits the same wall: without the udev rule the control
    interface is root-only and the app finds nothing. This screen states
    which of the three conditions failed and hands over the exact
    commands, rather than making someone read a README to learn that.
    """

    #: Where the rule has to live; the names come from the transport,
    #: which is what fails without it.
    RULE_NAME = transport.RULE_NAME
    RULE_DIR = transport.RULE_DIR

    look_again = Signal()
    #: a note for the window's status bar
    status = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(12)

        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 14, 18, 14)
        cl.setSpacing(10)

        title = QLabel("Setup")
        title.setStyleSheet("color:#cfe3ff; font-size:13px; font-weight:bold;")
        cl.addWidget(title)

        self.checks = QLabel()
        self.checks.setTextFormat(Qt.RichText)
        self.checks.setWordWrap(True)
        self.checks.setStyleSheet("color:#9fb3d0; font-size:12px;")
        cl.addWidget(self.checks)

        self.commands = QLabel()
        self.commands.setWordWrap(True)
        self.commands.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.commands.setStyleSheet(
            "color:#cfe3ff; font-size:11px; font-family:monospace;"
            " background:#061a33; border:1px solid #0d3a6b;"
            " border-radius:3px; padding:10px;"
        )
        cl.addWidget(self.commands)

        row = QHBoxLayout()
        self.install_button = primary_button("Install the rule…", self.install)
        row.addWidget(self.install_button)
        row.addWidget(ghost_button("Copy the commands", self.copy_commands))
        row.addWidget(ghost_button("Check again", self.look_again.emit))
        row.addStretch()
        cl.addLayout(row)
        lay.addWidget(card)
        lay.addStretch()

        self.refresh("starting…", False)

    # -- state ----------------------------------------------------------
    def rule_installed(self) -> bool:
        return transport.rule_installed()

    # -- doing it for them ----------------------------------------------
    def install(self) -> None:
        """Run the three commands under a graphical password prompt.

        pkexec rather than sudo: there is no terminal to type into, and
        the alternative is telling someone to go and find one.
        """
        helper = QStandardPaths.findExecutable("pkexec")
        if not helper:
            QMessageBox.information(
                self, "Run it in a terminal",
                "pkexec is not installed, so the rule cannot be put in "
                "place from here.\n\nRun this instead:\n\n"
                "    mk-k68-lightsmart install-udev"
            )
            return

        script = " && ".join(
            " ".join(part for part in command)
            for command in transport.install_rule_commands()
        )
        self.install_button.setEnabled(False)
        self.status.emit("installing the rule — answer the password prompt…")

        self._install = QProcess(self)
        self._install.finished.connect(self._installed)
        self._install.start(helper, ["sh", "-c", script])

    def _installed(self, code: int, _status) -> None:
        self.install_button.setEnabled(True)
        if code:
            # 126 is pkexec's "the dialog was dismissed".
            self.status.emit("the rule was not installed"
                             + (" — cancelled" if code == 126 else
                                f" (exit {code})"))
        else:
            self.status.emit("rule installed — looking for the keyboard…")
            self.look_again.emit()

    def refresh(self, device_status: str, connected: bool) -> None:
        """Redraw from the three things that have to be true."""
        rule = self.rule_installed()
        rows = [
            self._row(transport.keyboard_present(),
                      "Keyboard on the bus — it has to be on the cable, "
                      "with the switch in wired mode"),
            self._row(rule, f"udev rule installed in {self.RULE_DIR}/"
                            f"{self.RULE_NAME}"),
            self._row(connected, f"Control interface: {device_status}"),
        ]
        self.checks.setText("<br>".join(rows))
        self.commands.setText(self.command_text())

    @staticmethod
    def _row(ok: bool, text: str) -> str:
        mark = "✓" if ok else "•"
        colour = "#3ddc84" if ok else "#6a7d96"
        return f'<span style="color:{colour}">{mark}</span>&nbsp; {text}'

    def command_text(self) -> str:
        """The same three commands, for a terminal."""
        return "\n".join(
            "sudo " + " ".join(command)
            for command in transport.install_rule_commands()
        )

    def copy_commands(self) -> None:
        QApplication.clipboard().setText(self.command_text())


class MainWindow(QMainWindow):
    #: Requests for the device, emitted from this thread and delivered on
    #: the worker's. Qt queues them, which is also what keeps two
    #: transfers from interleaving on the wire.
    request_open = Signal()
    request_key = Signal(int, int, int, int)
    request_effect = Signal(int, int, int, int, int, int, int)
    request_upload = Signal(object)
    request_keymap = Signal(object, bool)

    NAV = [
        ("⌂", "Home"),
        ("⌨", "Keys"),
        ("≡", "Macro"),
        ("✺", "Light"),
        ("◉", "User"),
        ("❖", "Custom"),
        ("♫", "Music"),
        ("░", "Matrix"),
    ]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("MK-K68-LightSmart")
        self.resize(1268, 620)
        self.colour = QColor("#ff0000")
        # The keyboard is 140 ms per key and minutes per animation, so it
        # lives on its own thread; this window only sends and listens.
        self.device = DeviceThread()
        self._connected = False
        self._uploading = False

        root = QWidget()
        root.setStyleSheet(f"background: {BG.name()};")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_topbar())

        # Built before the screens rather than after them, although it
        # sits at the bottom: a screen can report something while it is
        # still being built, and a status sink that does not exist yet
        # turns that into an exception inside a Qt slot, where it is
        # printed and swallowed instead of failing loudly.
        statusbar = self._build_statusbar()

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        self.pages = QStackedWidget()
        self.light_page = LightPage(lambda: self.colour)
        self.light_page.apply_requested.connect(self.apply_light)
        self.matrix_page = MatrixPage(lambda: self.colour)
        self.matrix_page.upload_requested.connect(self.start_upload)
        self.matrix_page.cancel_requested.connect(self.cancel_upload)
        self.matrix_page.status.connect(self.set_status)
        # Imported here rather than at the top: the screens take this
        # module's chrome, so they can only be loaded once it exists.
        from . import screens

        self.keys_page = screens.KeysPage()
        self.keys_page.status.connect(self.set_status)
        self.keys_page.write_requested.connect(self.request_keymap)

        self.custom_page = screens.CustomPage(lambda: self.colour)
        self.custom_page.status.connect(self.set_status)
        self.custom_page.apply_key.connect(self.on_custom_key)

        self.macro_page = screens.MacroPage()
        self.macro_page.status.connect(self.set_status)

        self.profiles_page = screens.ProfilesPage()
        self.profiles_page.status.connect(self.set_status)
        # Switching a scheme is a replay, because the keyboard has no
        # command for it. The screens themselves re-read the scheme
        # when they next come up, so the only thing to do here is put
        # it on the hardware.
        self.profiles_page.replay_requested.connect(self.replay_scheme)
        self.profiles_page.scheme_changed.connect(self.on_scheme_changed)

        self.music_page = screens.MusicPage()
        self.music_page.status.connect(self.set_status)
        # The same handler as the custom screen: both are painting
        # keys, and the worker coalesces them per key either way.
        self.music_page.apply_key.connect(self.on_custom_key)

        # The rail's sections, in its order. Every one has a screen now.
        built = {0: self._build_home(), 1: self.keys_page,
                 2: self.macro_page, 3: self.light_page,
                 4: self.profiles_page, 5: self.custom_page,
                 6: self.music_page, 7: self.matrix_page}
        for index in range(len(self.NAV)):
            self.pages.addWidget(
                built.get(index) or self._build_placeholder(index))
        self.setup_page = SetupPage()
        self.setup_page.look_again.connect(self.reconnect)
        self.setup_page.status.connect(self.set_status)
        self.pages.addWidget(self.setup_page)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        outer.addWidget(statusbar)

        self._wire_device()
        self.set_status("looking for the keyboard…")
        self.device.start()

    # -- chrome ---------------------------------------------------------
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(56)
        bar.setStyleSheet(f"background: {BG.name()};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 14, 0)

        mark = QLabel("MK·K68")
        f = QFont()
        f.setPointSizeF(13)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        mark.setFont(f)
        mark.setStyleSheet(f"color: {ACCENT.name()};")
        lay.addWidget(mark)
        lay.addStretch()

        title = QLabel("L I G H T S M A R T")
        tf = QFont()
        tf.setPointSizeF(12)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(
            f"color: {WORDMARK.lighter(150).name()};"
            f" background: {PANEL.name()}; padding: 8px 48px;"
            " border: 1px solid #0d3a6b; border-radius: 3px;"
        )
        lay.addWidget(title)
        lay.addStretch()

        spacer = QLabel("MK·K68")
        spacer.setFont(f)
        spacer.setStyleSheet("color: transparent;")
        lay.addWidget(spacer)
        return bar

    def _build_sidebar(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(50)
        side.setStyleSheet(f"background: {SIDEBAR.name()};")
        lay = QVBoxLayout(side)
        lay.setContentsMargins(0, 6, 0, 10)
        lay.setSpacing(0)

        self.nav_buttons: list[SidebarButton] = []
        for i, (glyph, caption) in enumerate(self.NAV):
            b = SidebarButton(glyph, caption)
            b.clicked.connect(lambda _=False, n=i: self.select_page(n))
            lay.addWidget(b)
            self.nav_buttons.append(b)
        self.nav_buttons[0].setChecked(True)

        lay.addStretch()
        gear = SidebarButton("⚙", "Setup")
        gear.setCheckable(False)
        gear.setToolTip("Device and permissions")
        gear.clicked.connect(self.show_setup)
        lay.addWidget(gear)
        return side

    def _build_statusbar(self) -> QWidget:
        bar = QWidget()
        bar.setFixedHeight(28)
        bar.setStyleSheet(f"background: {SIDEBAR.darker(130).name()};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        self.status = QLabel("starting…")
        self.status.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
        lay.addWidget(self.status)
        lay.addStretch()
        self.last_packet = QLabel("")
        self.last_packet.setStyleSheet(
            "color: #44607f; font-size: 11px; font-family: monospace;"
        )
        lay.addWidget(self.last_packet)
        return bar

    # -- pages ----------------------------------------------------------
    def _build_home(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 14, 20, 14)
        lay.setSpacing(12)

        board = Card()
        bl = QVBoxLayout(board)
        bl.setContentsMargins(18, 14, 18, 14)
        bl.setSpacing(10)

        self.strip = DotMatrixStrip()
        self.strip.pixel_clicked.connect(self.on_pixel)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.strip)
        row.addStretch()
        bl.addLayout(row)
        bl.addLayout(self._build_strip_controls())

        self.keyboard = KeyboardView()
        self.keyboard.key_clicked.connect(self.on_key)
        krow = QHBoxLayout()
        krow.addStretch()
        krow.addWidget(self.keyboard)
        krow.addStretch()
        bl.addLayout(krow)
        lay.addWidget(board)

        lay.addWidget(self._build_controls())
        lay.addStretch()
        return page

    def _build_controls(self) -> QWidget:
        card = Card()
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.setSpacing(14)

        def label(text: str) -> QLabel:
            lb = QLabel(text)
            lb.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
            return lb

        lay.addWidget(label("Colour"))
        self.swatch = QPushButton()
        self.swatch.setFixedSize(52, 26)
        self.swatch.setCursor(Qt.PointingHandCursor)
        self.swatch.clicked.connect(self.pick_colour)
        self._paint_swatch()
        lay.addWidget(self.swatch)

        lay.addWidget(label("Effect"))
        self.effect_box = QComboBox()
        # In the vendor's own order, each carrying its mode byte -- which
        # is not the row: the driver keeps the mode as the list item's
        # data, and Close Backlight is 0x00 while the row before it is
        # 0x13.
        for effect in protocol.EFFECTS:
            self.effect_box.addItem(effect.name, effect.mode)
        self.effect_box.setFixedWidth(150)
        self.effect_box.setStyleSheet(
            "QComboBox { background:#0a2444; color:#cfe3ff; border:1px solid #0d3a6b;"
            " border-radius:3px; padding:4px 8px; font-size:11px; }"
            "QComboBox QAbstractItemView { background:#0a2444; color:#cfe3ff;"
            " selection-background-color:#10a9fd; }"
        )
        lay.addWidget(self.effect_box)

        lay.addWidget(label("Brightness"))
        self.brightness = QSlider(Qt.Horizontal)
        self.brightness.setRange(0, 15)
        self.brightness.setValue(10)
        self.brightness.setFixedWidth(110)
        lay.addWidget(self.brightness)

        lay.addWidget(label("Speed"))
        self.speed = QSlider(Qt.Horizontal)
        self.speed.setRange(0, 15)
        self.speed.setValue(8)
        self.speed.setFixedWidth(110)
        lay.addWidget(self.speed)

        lay.addStretch()

        lay.addWidget(primary_button("Apply effect", self.apply_effect))
        return card

    def _build_strip_controls(self) -> QHBoxLayout:
        """Brightness, upload, progress and cancel, under the strip.

        The strip is the one surface that is *edited* rather than driven:
        clicking a cell changes a drawing here, and nothing reaches the
        keyboard until Upload sends the whole frame.
        """
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch()

        cap = QLabel("Strip brightness")
        cap.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")
        row.addWidget(cap)

        self.strip_brightness = QSlider(Qt.Horizontal)
        self.strip_brightness.setRange(0, 100)
        self.strip_brightness.setValue(100)
        self.strip_brightness.setFixedWidth(110)
        self.strip_brightness.setToolTip("Scales every pixel on upload, 0-100%")
        row.addWidget(self.strip_brightness)

        self.upload_progress = QProgressBar()
        self.upload_progress.setFixedWidth(170)
        self.upload_progress.setFixedHeight(16)
        self.upload_progress.setTextVisible(False)
        self.upload_progress.setVisible(False)
        self.upload_progress.setStyleSheet(
            "QProgressBar { background:#0a2444; border:1px solid #0d3a6b;"
            " border-radius:3px; }"
            "QProgressBar::chunk { background:#10a9fd; border-radius:2px; }"
        )
        row.addWidget(self.upload_progress)

        self.cancel_button = ghost_button("Cancel", self.cancel_upload)
        self.cancel_button.setVisible(False)
        row.addWidget(self.cancel_button)

        self.clear_button = ghost_button("Clear", self.clear_strip)
        row.addWidget(self.clear_button)

        self.upload_button = primary_button("Upload to strip", self.upload_strip)
        self.upload_button.setEnabled(False)
        row.addWidget(self.upload_button)

        row.addStretch()
        return row

    #: What each unbuilt section is waiting on. "Not built yet" tells
    #: nobody anything; these say which part of the protocol is missing
    #: and what already works, so the gap looks measured rather than
    #: abandoned.
    PENDING = {
        1: ("Key remapping",
            "The block is decoded: 694 bytes, four per key, and the slot "
            "each key sits in. Of those, 576 are what actually go to the "
            "keyboard.\n\n"
            "The write works from the command line — 'remap --set "
            "Caps=0xe0', and 'remap --defaults' to put it back. The "
            "reset turned out not to be a command at all: the vendor's "
            "own reset writes that same default map, so the way back is "
            "the write.\n\n"
            "What is missing here is the screen: a keyboard to click and "
            "a key to pick for each one."),
        2: ("Macros",
            "The command and its framing are known (0x15, sent in 64-byte "
            "blocks). The event encoding is not: a key press emits a 0x50 "
            "marker and two bytes whose meaning is still open."),
        4: ("User profiles",
            "The keyboard holds several profiles and the vendor software "
            "switches between them. Which command does that has not been "
            "found yet."),
        5: ("Custom lighting",
            "Per-key colours already work on the Home screen — this is "
            "where a whole layout would be painted and stored as a "
            "profile, which needs the profile commands first."),
        6: ("Music mode",
            "The keyboard can react to sound. The host does the listening "
            "and sends colours, so this needs both an audio capture and "
            "the command the vendor app uses, neither of which is done."),
    }

    def _build_placeholder(self, index: int = -1) -> QWidget:
        title, detail = self.PENDING.get(
            index, ("Not built yet", "This section has no screen yet."))

        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 14, 20, 14)
        card = Card()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(20, 18, 20, 18)
        inner.setSpacing(10)

        heading = QLabel(title)
        heading.setStyleSheet("color:#cfe3ff; font-size:13px; font-weight:bold;")
        inner.addWidget(heading)

        body = QLabel(detail)
        body.setWordWrap(True)
        body.setStyleSheet(f"color: {MUTED.name()}; font-size: 12px;")
        inner.addWidget(body)

        link = QLabel("The state of every part is in docs/PROTOCOL.md.")
        link.setStyleSheet("color:#44607f; font-size: 11px;")
        inner.addWidget(link)
        lay.addWidget(card)
        lay.addStretch()
        return page

    # -- behaviour ------------------------------------------------------
    def select_page(self, n: int) -> None:
        for i, b in enumerate(self.nav_buttons):
            b.setChecked(i == n)
        self.pages.setCurrentIndex(n)

    def _paint_swatch(self) -> None:
        self.swatch.setStyleSheet(
            f"background: {self.colour.name()};"
            " border: 1px solid #2a4a72; border-radius: 3px;"
        )

    def pick_colour(self) -> None:
        picked = QColorDialog.getColor(self.colour, self, "Pick a colour")
        if picked.isValid():
            self.colour = picked
            self._paint_swatch()

    # -- the device, which lives on the worker thread --------------------
    def _wire_device(self) -> None:
        w = self.device.worker
        w.connected.connect(self.on_connected)
        w.disconnected.connect(self.on_disconnected)
        w.status.connect(self.set_status)
        w.key_applied.connect(self.on_key_applied)
        w.progress.connect(self.on_progress)
        w.upload_finished.connect(self.on_upload_finished)
        # These cross into the worker's thread, so Qt queues them and the
        # worker runs them one after another -- which is also what keeps
        # two transfers from interleaving on the wire.
        self.request_open.connect(w.open)
        self.request_key.connect(w.set_key_colour)
        self.request_effect.connect(w.set_effect)
        self.request_upload.connect(w.upload)
        self.request_keymap.connect(w.write_keymap)

    def reconnect(self) -> None:
        self.set_status("looking for the keyboard…")
        self.setup_page.refresh("looking…", False)
        self.request_open.emit()

    def show_setup(self) -> None:
        """Open the setup screen, which is not one of the rail's pages."""
        for b in self.nav_buttons:
            b.setChecked(False)
        self.pages.setCurrentWidget(self.setup_page)

    def on_connected(self, path: str) -> None:
        self._connected = True
        self.upload_button.setEnabled(not self._uploading)
        self.matrix_page.set_connected(not self._uploading)
        self.setup_page.refresh(path, True)
        self.set_status(f"connected — {path}", ok=True)

    def on_disconnected(self, why: str) -> None:
        self._connected = False
        if self._uploading:
            self._set_uploading(False)
        self.upload_button.setEnabled(False)
        self.matrix_page.set_connected(False)
        self.setup_page.refresh(why, False)
        self.set_status(
            f"{why} — plug it in by cable, wired mode, then press Setup",
            ok=False,
        )

    def closeEvent(self, event) -> None:
        self.matrix_page.stop()
        # Cancels any upload, then joins the thread, so the descriptor is
        # closed by the thread that opened it.
        if not self.device.stop():
            print("device thread would not stop; terminated it", file=sys.stderr)
        super().closeEvent(event)

    def set_status(self, text: str, ok: bool | None = None) -> None:
        """Report. ``ok`` left out is a neutral note, not an outcome."""
        colour = MUTED.name() if ok is None else ("#3ddc84" if ok else "#ff6b6b")
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-size: 11px;")

    # -- home screen ----------------------------------------------------
    def on_key(self, key: kb_layout.Key) -> None:
        rgb = (self.colour.red(), self.colour.green(), self.colour.blue())
        frame = protocol.data_frame(
            protocol.MODE_SINGLE_KEY, rgb, p8=key.light_index, p10=0x0F, p11=0x0F
        )
        self.last_packet.setText(" ".join(f"{b:02X}" for b in frame[:16]))
        self.request_key.emit(key.light_index, *rgb)

    def on_scheme_changed(self, scheme) -> None:
        """Say which scheme is in use now.

        Nothing else is needed: each screen re-reads the scheme when it
        next comes up, so they cannot show another one's work, and
        putting it on the keyboard is a separate ask -- the profiles
        screen decides whether a switch replays, because half of a
        replay is a persistent write.
        """
        self.set_status(f"scheme: {scheme.name}")

    def replay_scheme(self, write_keymap: bool = True) -> None:
        """Put the active scheme back on the keyboard.

        A scheme is a host-side bundle because the keyboard has no
        command to switch one — the vendor's own driver sends it
        nothing when a scheme changes and re-sends the contents instead
        (docs/PROTOCOL.md §10). So "switching" means exactly this:
        the keymap as one block, then the painted layout a key at a
        time.

        ``write_keymap`` is separate because the two halves differ in
        how much they cost to be wrong. The lighting is gone at the
        next effect; the keymap outlives the power cycle. A caller that
        has not asked anybody should pass False.
        """
        from . import store as scheme_store

        scheme = scheme_store.shared().current
        changes = scheme.remap_changes()
        if write_keymap and changes:
            self.request_keymap.emit(protocol.remap_block(changes), False)
        for index, rgb in scheme.lighting.items():
            self.request_key.emit(
                index, (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)
        self.set_status(
            f"{scheme.name}: {len(changes)} remapped, "
            f"{len(scheme.lighting)} lit")

    def on_custom_key(self, light_index: int, colour: QColor) -> None:
        """Send one key of a painted layout.

        The worker coalesces these by key, so sending a whole board at
        once costs one request per key rather than one per repaint.
        """
        self.request_key.emit(
            light_index, colour.red(), colour.green(), colour.blue())

    def on_key_applied(self, light_index: int, rgb: int) -> None:
        """Paint a key only once the keyboard has taken its colour."""
        self.keyboard.colours[light_index] = QColor(
            (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF
        )
        self.keyboard.update()

    def on_pixel(self, col: int, row: int) -> None:
        self.strip.pixels[(col, row)] = QColor(self.colour)
        self.strip.update()
        self.set_status(f"strip ({col},{row}) painted — Upload sends the frame")

    def clear_strip(self) -> None:
        self.strip.clear()
        self.set_status("strip cleared here — Upload sends the blank frame")

    def strip_animation(self) -> matrix.Animation:
        """What the editor shows, as a one-frame animation."""
        frame = matrix.Frame()
        for (col, row), colour in self.strip.pixels.items():
            frame[col, row] = colour.rgb() & 0xFFFFFF
        return matrix.Animation(
            frames=[frame], brightness=self.strip_brightness.value()
        )

    def upload_strip(self) -> None:
        self.start_upload(self.strip_animation())

    def start_upload(self, animation) -> None:
        """Send an animation, from whichever screen built it."""
        try:
            blocks = animation.block_count()
        except ValueError as exc:
            self.set_status(f"nothing to upload: {exc}", ok=False)
            return
        self._set_uploading(True)
        self.on_progress(0, blocks)
        self.set_status(
            f"uploading {len(animation.frames)} frame(s), {blocks} blocks…"
        )
        self.request_upload.emit(animation)

    def cancel_upload(self) -> None:
        # Through an Event, not the queue: an upload in progress is
        # exactly what is *not* reading the queue.
        self.device.worker.cancel()
        self.cancel_button.setEnabled(False)
        # The worker reports what the cancel actually costs: the
        # firmware counts blocks and will not listen again until it has
        # them all, so stopping early still has to feed it the rest.
        self.set_status("cancelling — the keyboard must be fed the rest…")

    def _set_uploading(self, on: bool) -> None:
        self._uploading = on
        self.upload_button.setEnabled(self._connected and not on)
        self.clear_button.setEnabled(not on)
        self.upload_progress.setVisible(on)
        self.cancel_button.setVisible(on)
        self.cancel_button.setEnabled(on)
        self.matrix_page.set_uploading(on, self._connected)

    def on_progress(self, sent: int, total: int) -> None:
        for bar in (self.upload_progress, self.matrix_page.upload_progress):
            if bar.maximum() != total:
                bar.setRange(0, total)
            bar.setValue(sent)

    def on_upload_finished(self, ok: bool) -> None:
        # The worker has already said what happened; this only hands the
        # controls back.
        self._set_uploading(False)

    def apply_effect(self) -> None:
        mode = self.effect_box.currentData()
        rgb = (self.colour.red(), self.colour.green(), self.colour.blue())
        frame = protocol.data_frame(
            mode, rgb, p9=self.brightness.value(), p10=self.speed.value()
        )
        self.last_packet.setText(" ".join(f"{b:02X}" for b in frame[:16]))
        self.request_effect.emit(
            mode, *rgb, self.brightness.value(), self.speed.value(), 0
        )

    def apply_light(self, mode: int, rgb: int, brightness: int, speed: int,
                    direction: int) -> None:
        """The Light screen's own request, which carries a direction."""
        name = protocol.EFFECT_BY_MODE.get(mode)
        self.last_packet.setText(" ".join(
            f"{b:02X}" for b in protocol.data_frame(
                mode, ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF),
                p9=brightness, p10=speed, p11=direction)[:16]
        ))
        self.set_status(f"applying {name.name if name else f'{mode:#04x}'}…")
        self.request_effect.emit(
            mode, (rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF,
            brightness, speed, direction,
        )


def main(argv: list[str] | None = None) -> int:
    app = QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("MK-K68-LightSmart")
    app.setApplicationDisplayName("MK-K68-LightSmart")
    # Wayland matches a window to its icon through the desktop file name,
    # so without this the dock shows a generic icon.
    app.setDesktopFileName("mk-k68-lightsmart")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
