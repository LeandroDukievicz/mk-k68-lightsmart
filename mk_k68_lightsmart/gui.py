"""Desktop front end.

Lays out the same screens as the vendor's Windows app -- same sidebar,
same 1268x563 proportions, same palette, which was sampled straight off
the manual's screenshots. The artwork is drawn rather than copied, so
nothing of the manufacturer's ships here.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
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
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import layout as kb_layout
from . import protocol
from .transport import DeviceGone, DeviceNotFound, TransportError

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


class MainWindow(QMainWindow):
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
        self.kb: protocol.Keyboard | None = None
        self.colour = QColor("#ff0000")

        root = QWidget()
        root.setStyleSheet(f"background: {BG.name()};")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar())

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_home())
        for _ in range(len(self.NAV) - 1):
            self.pages.addWidget(self._build_placeholder())
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        outer.addWidget(self._build_statusbar())
        self.connect_device()

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
        self.effect_box.addItems([
            "Static", "SingleOn", "SingleOff", "Glittering", "Falling",
            "Colourful", "Breath", "Spectrum", "Outward", "Scrolling",
            "Rolling", "Rotating", "Explode", "Launch", "Ripples",
            "Flowing", "Pulsating", "Tilt", "Shuttle", "Close Backlight",
            "Inwards", "Floweriness",
        ])
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

        apply_effect = QPushButton("Apply effect")
        apply_effect.setCursor(Qt.PointingHandCursor)
        apply_effect.setStyleSheet(
            "QPushButton { background:#10a9fd; color:#04121f; border:none;"
            " border-radius:3px; padding:7px 16px; font-weight:bold; font-size:11px; }"
            "QPushButton:hover { background:#3dbdff; }"
        )
        apply_effect.clicked.connect(self.apply_effect)
        lay.addWidget(apply_effect)
        return card

    def _build_placeholder(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lb = QLabel("Not built yet.")
        lb.setAlignment(Qt.AlignCenter)
        lb.setStyleSheet(f"color: {MUTED.name()}; font-size: 13px;")
        lay.addWidget(lb)
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

    def connect_device(self) -> None:
        if self.kb is not None:
            self.kb.close()
            self.kb = None
        try:
            self.kb = protocol.Keyboard()
            self.set_status(f"connected — {self.kb.t.path}", ok=True)
        except (DeviceNotFound, TransportError) as exc:
            self.kb = None
            self.set_status(str(exc), ok=False)

    def closeEvent(self, event) -> None:
        if self.kb is not None:
            self.kb.close()
            self.kb = None
        super().closeEvent(event)

    def set_status(self, text: str, ok: bool) -> None:
        colour = "#3ddc84" if ok else "#ff6b6b"
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {colour}; font-size: 11px;")

    def _send(self, what: str, fn) -> bool:
        """Run a device call, reporting the outcome. True if it landed."""
        if self.kb is None:
            self.set_status(
                "no keyboard — plug it in by cable, wired mode, "
                "then press Setup",
                ok=False,
            )
            return False
        try:
            fn()
        except DeviceGone as exc:
            # The transport already closed itself; drop the handle so the
            # next attempt -- or the Setup button -- can reconnect.
            self.kb = None
            self.set_status(f"{exc}. Press Setup to look again.", ok=False)
            return False
        except TransportError as exc:
            self.set_status(f"{what} failed: {exc}", ok=False)
            return False
        self.set_status(f"{what} sent", ok=True)
        return True

    def on_key(self, key: kb_layout.Key) -> None:
        rgb = (self.colour.red(), self.colour.green(), self.colour.blue())
        frame = protocol.data_frame(
            protocol.MODE_SINGLE_KEY, rgb, p8=key.light_index, p10=0x0F, p11=0x0F
        )
        self.last_packet.setText(" ".join(f"{b:02X}" for b in frame[:16]))
        # Paint only once the keyboard has taken it, so the picture cannot
        # claim a colour the hardware never got.
        landed = self._send(
            f"{key.ident} → {self.colour.name()}",
            lambda: self.kb.set_key_colour(key.light_index, rgb),
        )
        if landed:
            self.keyboard.colours[key.light_index] = QColor(self.colour)
            self.keyboard.update()

    def on_pixel(self, col: int, row: int) -> None:
        self.strip.pixels[(col, row)] = QColor(self.colour)
        self.strip.update()
        self.status.setText(
            f"strip pixel ({col},{row}) — upload format not decoded yet"
        )
        self.status.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px;")

    def apply_effect(self) -> None:
        mode = self.effect_box.currentIndex()
        rgb = (self.colour.red(), self.colour.green(), self.colour.blue())
        frame = protocol.data_frame(
            mode, rgb, p9=self.brightness.value(), p10=self.speed.value()
        )
        self.last_packet.setText(" ".join(f"{b:02X}" for b in frame[:16]))
        self._send(
            f"effect {self.effect_box.currentText()} (mode {mode:#04x})",
            lambda: self.kb.set_effect(
                mode, rgb,
                brightness=self.brightness.value(),
                speed=self.speed.value(),
            ),
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
