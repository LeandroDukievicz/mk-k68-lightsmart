"""Chrome the screens share: notes, cards, lists and combos."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QListWidget, QVBoxLayout, QWidget

from ..gui import MUTED, Card

PENDING_STYLE = (
    "color:#ffc46b; font-size:11px; background: rgba(90,60,0,70);"
    " border:1px solid #6b4a10; border-radius:4px; padding:7px 10px;"
)


def _note(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setStyleSheet(PENDING_STYLE)
    return lb


def _heading(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet("color:#cfe3ff; font-size:12px; font-weight:bold;")
    return lb


def _card(*rows) -> Card:
    """A card holding the widgets or layouts given, in order."""
    card = Card()
    inner = QVBoxLayout(card)
    inner.setContentsMargins(16, 14, 16, 14)
    inner.setSpacing(9)
    for row in rows:
        if isinstance(row, QWidget):
            inner.addWidget(row)
        else:
            inner.addLayout(row)
    return card


def _list() -> QListWidget:
    w = QListWidget()
    w.setStyleSheet(
        "QListWidget { background:#061634; color:#cfe3ff; font-size:11px;"
        " border:1px solid #0d3a6b; border-radius:4px; }"
        "QListWidget::item { padding:4px 6px; }"
        "QListWidget::item:selected { background:#0d3a6b; }"
    )
    return w


def _combo(items: list[str] | None = None) -> QComboBox:
    c = QComboBox()
    c.setStyleSheet(
        "QComboBox { background:#0a2444; color:#cfe3ff; border:1px solid #0d3a6b;"
        " border-radius:3px; padding:4px 8px; font-size:11px; }"
    )
    for text in items or []:
        c.addItem(text)
    return c


