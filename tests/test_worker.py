"""Checks the device thread's contract with the window.

The worker is the only thing that touches the descriptor, so what matters
here is not Qt plumbing but promises: that a key is painted only after the
keyboard took it, that a dead device is reported once and dropped, and
that a cancelled upload still closes its transaction -- otherwise the
keyboard sits on its loading screen.

Transfers go through the real :class:`protocol.Keyboard` over a fake
transport, so the envelope these tests see is the envelope the hardware
would see.
"""

from __future__ import annotations

import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mk_k68_lightsmart import matrix, protocol, transport

try:
    from mk_k68_lightsmart import worker
except ImportError:                                   # pragma: no cover
    worker = None

requires_qt = unittest.skipIf(worker is None, "PySide6 is not installed")


class FakeTransport:
    """Acks everything and remembers what it was asked to send."""

    def __init__(self, path: str = "/dev/hidraw-fake"):
        self.path = path
        self.sent: list[bytes] = []
        self.closed = False

    def set_feature(self, payload: bytes) -> None:
        if self.closed:
            raise transport.TransportError("fake transport is closed")
        self.sent.append(bytes(payload))

    def get_feature(self) -> bytes:
        reply = bytearray(transport.REPORT_SIZE)
        reply[transport.ACK_OFFSET] = transport.ACK_OK
        return bytes(reply)

    def close(self) -> None:
        self.closed = True

    # -- what the tests ask about ---------------------------------------
    def heads(self, n: int = 2) -> list[str]:
        return [" ".join(f"{b:02X}" for b in p[:n]) for p in self.sent]


class FailingTransport(FakeTransport):
    """Fails the nth transfer with ``exc``, counting from one."""

    def __init__(self, exc: Exception, on: int = 1):
        super().__init__()
        self.exc = exc
        self.on = on

    def set_feature(self, payload: bytes) -> None:
        super().set_feature(payload)
        if len(self.sent) == self.on:
            raise self.exc


class CancellingTransport(FakeTransport):
    """Cancels the worker partway through, as the Cancel button would."""

    def __init__(self, w, after: int):
        super().__init__()
        self.worker = w
        self.after = after

    def set_feature(self, payload: bytes) -> None:
        super().set_feature(payload)
        if len(self.sent) == self.after:
            self.worker.cancel()


class WorkerCase(unittest.TestCase):
    """Collects what a worker emitted, so each test can assert on it."""

    def make(self, t: FakeTransport | None = None):
        w = worker.DeviceWorker()
        self.events: dict[str, list] = {}

        def collect(name):
            return lambda *args: self.events.setdefault(name, []).append(args)

        for name in ("connected", "disconnected", "status", "key_applied",
                     "progress", "upload_finished"):
            getattr(w, name).connect(collect(name))

        if t is not None:
            w.kb = protocol.Keyboard(t)
        return w

    def one_frame(self) -> matrix.Animation:
        frame = matrix.Frame()
        frame[0, 0] = 0xFF0000
        return matrix.Animation(frames=[frame])


@requires_qt
class Connecting(WorkerCase):
    def test_open_reports_the_node_it_found(self):
        w = self.make()
        fake = FakeTransport("/dev/hidraw9")
        # Bound before the patch: the patch replaces this very attribute,
        # so a late lookup inside the factory would call the factory.
        real = protocol.Keyboard
        with unittest.mock.patch.object(
            worker.protocol, "Keyboard", lambda: real(fake)
        ):
            w.open()
        self.assertEqual(self.events["connected"], [("/dev/hidraw9",)])
        self.assertNotIn("disconnected", self.events)

    def test_open_reports_why_it_failed(self):
        w = self.make()

        def boom():
            raise transport.DeviceNotFound("no vendor interface")

        with unittest.mock.patch.object(worker.protocol, "Keyboard", boom):
            w.open()
        self.assertIsNone(w.kb)
        self.assertEqual(self.events["disconnected"], [("no vendor interface",)])

    def test_an_operation_without_a_keyboard_says_so(self):
        w = self.make()
        w.set_key_colour(1, 255, 0, 0)
        self.assertEqual(self.events["disconnected"], [("no keyboard",)])
        self.assertNotIn("key_applied", self.events)


@requires_qt
class KeyColours(WorkerCase):
    def test_sends_the_whole_envelope_and_reports_the_colour(self):
        t = FakeTransport()
        w = self.make(t)
        w.set_key_colour(5, 0x12, 0x34, 0x56)

        # The third transfer is the data frame: mode 0x80, then red.
        self.assertEqual(t.heads(), ["04 18", "04 13", "80 12", "04 02", "04 F0"])
        self.assertEqual(t.sent[2][:4], bytes([0x80, 0x12, 0x34, 0x56]))
        self.assertEqual(self.events["key_applied"], [(5, 0x123456)])

    def test_paints_nothing_when_the_keyboard_refuses(self):
        t = FailingTransport(transport.TransportError("nope"), on=1)
        w = self.make(t)
        w.set_key_colour(5, 255, 0, 0)

        self.assertNotIn("key_applied", self.events)
        self.assertFalse(self.events["status"][0][1])

    def test_a_vanished_device_is_dropped_and_reported_once(self):
        t = FailingTransport(transport.DeviceGone("unplugged"), on=1)
        w = self.make(t)
        w._pending.update({7: (1, 2, 3), 8: (4, 5, 6)})
        w.set_key_colour(5, 255, 0, 0)

        self.assertIsNone(w.kb)
        self.assertEqual(self.events["disconnected"], [("unplugged",)])
        # Nothing may be left queued for a keyboard that is gone.
        self.assertEqual(w._pending, {})

    def test_a_drag_keeps_the_last_colour_per_key_in_order(self):
        t = FakeTransport()
        w = self.make(t)
        # What a drag leaves queued while an earlier send is still on the
        # wire: Esc twice, then two more keys.
        w._pending.update({1: (1, 1, 1), 2: (2, 2, 2)})
        w._pending[1] = (9, 9, 9)
        w._drain()

        self.assertEqual(
            self.events["key_applied"], [(1, 0x090909), (2, 0x020202)]
        )


@requires_qt
class Uploads(WorkerCase):
    def test_reports_progress_and_finishes(self):
        t = FakeTransport()
        w = self.make(t)
        animation = self.one_frame()
        blocks = animation.block_count()

        w.upload(animation)

        self.assertEqual(self.events["upload_finished"], [(True,)])
        # One frame is 16 blocks, under PROGRESS_EVERY, so the only
        # update is the final one -- the bar must still reach the end.
        self.assertEqual(self.events["progress"][-1], (blocks, blocks))
        self.assertEqual(t.heads()[0], "04 18")
        self.assertEqual(t.heads()[-1], "04 F0")

    def test_progress_is_coalesced_on_a_long_upload(self):
        t = FakeTransport()
        w = self.make(t)
        frame = matrix.Frame()
        animation = matrix.Animation(frames=[frame] * 60)
        blocks = animation.block_count()

        w.upload(animation)

        self.assertGreater(blocks, worker.DeviceWorker.PROGRESS_EVERY)
        updates = self.events["progress"]
        self.assertLessEqual(len(updates), blocks // 64 + 2)
        self.assertEqual(updates[-1], (blocks, blocks))

    def test_cancelling_still_closes_the_transaction(self):
        w = self.make()
        t = CancellingTransport(w, after=4)
        w.kb = protocol.Keyboard(t)

        w.upload(self.one_frame())

        # The keyboard must not be left inside an open transaction.
        self.assertEqual(t.heads()[-1], "04 F0")
        self.assertEqual(self.events["upload_finished"], [(False,)])
        self.assertEqual(self.events["status"], [("upload cancelled", False)])
        # It stopped early: a full one-frame upload is 20 transfers.
        self.assertLess(len(t.sent), 20)

    def test_a_stale_cancel_does_not_kill_the_next_upload(self):
        t = FakeTransport()
        w = self.make(t)
        w.cancel()                      # e.g. the previous upload was stopped
        w.upload(self.one_frame())
        self.assertEqual(self.events["upload_finished"], [(True,)])

    def test_an_empty_animation_is_refused_without_touching_the_wire(self):
        t = FakeTransport()
        w = self.make(t)
        w.upload(matrix.Animation(frames=[]))

        self.assertEqual(t.sent, [])
        self.assertEqual(self.events["upload_finished"], [(False,)])
        self.assertFalse(self.events["status"][0][1])

    def test_a_device_lost_midway_is_reported_as_disconnected(self):
        w = self.make()
        t = FailingTransport(transport.DeviceGone("unplugged mid-upload"), on=4)
        w.kb = protocol.Keyboard(t)

        w.upload(self.one_frame())

        self.assertIsNone(w.kb)
        self.assertEqual(self.events["disconnected"], [("unplugged mid-upload",)])
        self.assertEqual(self.events["upload_finished"], [(False,)])


if __name__ == "__main__":
    unittest.main()
