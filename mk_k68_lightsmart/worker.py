"""The device, on a thread of its own.

Every transfer is paced 15 ms, and the envelope around a single key
colour is nine of them -- 140 ms measured on real hardware. A full strip
animation is 14,336 transfers, about three and a half minutes. None of
that can run on the Qt main thread.

One thread, not a pool. The transport reuses a single wire buffer, reads
the acknowledgement that follows each write, and the keyboard itself
holds state between ``04 18`` and ``04 F0`` -- so two senders would
interleave each other's bytes, each other's acks, and each other's
transactions. Serialising on one thread is the whole design.

The transport is created *inside* the worker and closed there, so the
descriptor is only ever touched by the thread that ioctls it.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, Signal, Slot

from . import protocol
from .matrix import Animation
from .transport import DeviceGone, DeviceNotFound, TransportError


class Cancelled(Exception):
    """Raised out of the progress callback to abort an upload."""


@dataclass(frozen=True)
class KeyColour:
    light_index: int
    rgb: tuple[int, int, int]


class DeviceWorker(QObject):
    """Owns the keyboard handle and does every transfer."""

    #: path of the control interface
    connected = Signal(str)
    #: why we have no keyboard
    disconnected = Signal(str)
    #: human-readable outcome, and whether it was a success
    status = Signal(str, bool)
    #: light index, and the 0xRRGGBB the keyboard accepted for it
    key_applied = Signal(int, int)
    #: blocks sent, blocks total
    progress = Signal(int, int)
    #: an upload ended; True if it completed
    upload_finished = Signal(bool)

    #: Emit at most one progress update per this many blocks. 14,336
    #: repaints would cost more than the transfers.
    PROGRESS_EVERY = 64

    def __init__(self) -> None:
        super().__init__()
        self.kb: protocol.Keyboard | None = None
        self._cancel = threading.Event()
        # Key colours arrive faster than they can be sent while dragging,
        # so only the latest value per key is kept.
        self._pending: dict[int, tuple[int, int, int]] = {}
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------
    @Slot()
    def open(self) -> None:
        """Connect. Runs on the worker thread, via QThread.started."""
        self.close()
        try:
            self.kb = protocol.Keyboard()
        except (DeviceNotFound, TransportError) as exc:
            self.kb = None
            self.disconnected.emit(str(exc))
        else:
            self.connected.emit(self.kb.t.path)

    @Slot()
    def close(self) -> None:
        if self.kb is not None:
            self.kb.close()
            self.kb = None

    def cancel(self) -> None:
        """Ask an upload to stop. Safe to call from any thread."""
        self._cancel.set()

    # -- operations -----------------------------------------------------
    def _guard(self, what: str, fn) -> bool:
        if self.kb is None:
            self.disconnected.emit("no keyboard")
            return False
        try:
            fn()
        except DeviceGone as exc:
            self.kb = None          # the transport already closed itself
            self.disconnected.emit(str(exc))
            return False
        except (TransportError, ValueError) as exc:
            self.status.emit(f"{what} failed: {exc}", False)
            return False
        self.status.emit(f"{what} sent", True)
        return True

    @Slot(int, int, int, int)
    def set_key_colour(self, light_index: int, r: int, g: int, b: int) -> None:
        """Queue one key's colour, keeping only the latest per key.

        A drag across the board queues hundreds of these; sending every
        one would put the UI minutes behind the cursor.
        """
        with self._lock:
            self._pending[light_index] = (r, g, b)
        self._drain()

    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._pending:
                    return
                # Oldest first. ``popitem`` would take the newest, so a
                # drag across the board would light its keys backwards.
                index = next(iter(self._pending))
                rgb = self._pending.pop(index)
            if not self._guard(
                f"LED {index}", lambda: self.kb.set_key_colour(index, rgb)
            ):
                with self._lock:
                    self._pending.clear()
                return
            # The window paints from this, not from the click, so it can
            # never show a colour the keyboard refused.
            self.key_applied.emit(index, rgb[0] << 16 | rgb[1] << 8 | rgb[2])

    @Slot(int, int, int, int, int, int, int)
    def set_effect(self, mode: int, r: int, g: int, b: int,
                   brightness: int, speed: int, direction: int = 0) -> None:
        self._guard(
            f"effect {mode:#04x}",
            lambda: self.kb.set_effect(
                mode, (r, g, b), brightness=brightness, speed=speed,
                direction=direction
            ),
        )

    @Slot(object)
    def upload(self, animation: Animation) -> None:
        """Push an animation to the strip, reporting progress."""
        if self.kb is None:
            self.disconnected.emit("no keyboard")
            self.upload_finished.emit(False)
            return

        self._cancel.clear()
        last = 0
        at_stop = {"sent": 0, "total": 0}

        def report(sent: int, total: int) -> None:
            nonlocal last
            at_stop["sent"], at_stop["total"] = sent, total
            if self._cancel.is_set():
                # Raising here unwinds into upload_animation's drain: the
                # firmware is still counting blocks and will not answer a
                # command until it has them all, so stopping costs the
                # same 15 ms each on the way out. Say so rather than look
                # hung.
                owed = total - sent
                self.status.emit(
                    f"cancelling — the keyboard still wants {owed} blocks "
                    f"(~{owed * 0.015:.0f}s)", False
                )
                raise Cancelled
            if sent - last >= self.PROGRESS_EVERY or sent == total:
                last = sent
                self.progress.emit(sent, total)

        def draining(done: int, remaining: int) -> None:
            """Keep the bar moving while the blanks go out."""
            if done % self.PROGRESS_EVERY and done != remaining:
                return
            total = at_stop["total"] or at_stop["sent"] + remaining
            self.progress.emit(at_stop["sent"] + done, total)

        try:
            self.kb.upload_animation(animation, progress=report, drain=draining)
        except Cancelled:
            self.status.emit("upload cancelled", False)
            self.upload_finished.emit(False)
        except DeviceGone as exc:
            self.kb = None
            self.disconnected.emit(str(exc))
            self.upload_finished.emit(False)
        except (TransportError, ValueError) as exc:
            self.status.emit(f"upload failed: {exc}", False)
            self.upload_finished.emit(False)
        else:
            self.status.emit("animation applied", True)
            self.upload_finished.emit(True)


class DeviceThread:
    """Holds the worker and its thread, and shuts both down cleanly."""

    def __init__(self) -> None:
        self.thread = QThread()
        self.thread.setObjectName("kt68-device")
        self.worker = DeviceWorker()
        self.worker.moveToThread(self.thread)
        # Opening on `started` keeps the descriptor on the worker thread.
        self.thread.started.connect(self.worker.open)
        self.thread.finished.connect(self.worker.close)

    def start(self) -> None:
        self.thread.start()

    def stop(self, timeout_ms: int = 5000) -> bool:
        """Cancel any upload, then stop the thread and wait for it."""
        self.worker.cancel()
        self.thread.quit()
        if not self.thread.wait(timeout_ms):
            self.thread.terminate()
            self.thread.wait(1000)
            return False
        return True
