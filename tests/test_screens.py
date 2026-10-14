"""The contract between the screens and the window that wires them.

Each screen now has its own test file, which is where its behaviour is
checked. What is left here is the part no single screen can test: that
``MainWindow`` can still connect every one of them. A screen that
quietly renames a signal or grows a constructor argument breaks the
app at start-up and passes all of its own tests, so this file exists
to catch exactly that.

Nothing here clicks anything. Several screens now confirm destructive
actions with a modal dialog, and a modal dialog under the offscreen
platform waits forever.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QMetaMethod, Signal
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

if QApplication is not None:
    from mk_k68_lightsmart import gui, screens

app = None


def setUpModule() -> None:
    global app
    if QApplication is not None:
        app = QApplication.instance() or QApplication([])


def connects(page, name: str) -> bool:
    """Whether ``MainWindow`` could connect ``page.<name>``.

    Asking the meta-object what it calls a signal is a test of Qt. The
    thing that actually breaks the app is ``connect`` not being there,
    so that is what is checked -- and it is checked by connecting,
    because a bound signal is the only object that will accept it.
    """
    signal = getattr(page, name, None)
    if signal is None or not hasattr(signal, "connect"):
        return False
    signal.connect(lambda *_: None)
    return True


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class EveryScreenBuilds(unittest.TestCase):
    """The cheapest regression there is, and the one that bites first."""

    def pages(self) -> dict[str, object]:
        return {
            "keys": screens.KeysPage(),
            "macro": screens.MacroPage(),
            "profiles": screens.ProfilesPage(),
            "custom": screens.CustomPage(lambda: QColor("#ff0000")),
            "music": screens.MusicPage(),
        }

    def test_all_five_construct_and_lay_themselves_out(self):
        for name, page in self.pages().items():
            self.assertTrue(page.layout().count() > 0, name)

    def test_four_of_them_take_no_arguments(self):
        # Custom is the exception: it is handed the window's current
        # colour. If another screen grows an argument, MainWindow stops
        # being able to build it.
        screens.KeysPage()
        screens.MacroPage()
        screens.ProfilesPage()
        screens.MusicPage()


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheWiringContract(unittest.TestCase):
    """Every signal MainWindow connects, checked where it is declared."""

    def test_every_screen_can_say_something(self):
        for page in (screens.KeysPage(), screens.MacroPage(),
                     screens.ProfilesPage(), screens.MusicPage(),
                     screens.CustomPage(lambda: QColor("#ff0000"))):
            self.assertTrue(connects(page, "status"), type(page).__name__)

    def test_keys_still_asks_for_the_write(self):
        self.assertTrue(connects(screens.KeysPage(), "write_requested"))

    def test_both_lighting_screens_send_a_key_the_same_way(self):
        # Custom and Music are wired to one handler, so they have to
        # agree on the signal.
        self.assertTrue(connects(
            screens.CustomPage(lambda: QColor("#ff0000")), "apply_key"))
        self.assertTrue(connects(screens.MusicPage(), "apply_key"))

    def test_profiles_asks_for_a_replay_and_announces_the_switch(self):
        page = screens.ProfilesPage()
        self.assertTrue(connects(page, "replay_requested"))
        self.assertTrue(connects(page, "scheme_changed"))


def own_signals(page) -> dict[str, bool]:
    """Each signal this project declares on ``page``, and whether it is
    connected.

    Only the signals declared in our own classes: a QWidget arrives
    carrying a dozen of Qt's, and nothing here is wired to those.
    """
    names = {
        name
        for cls in type(page).__mro__
        if cls.__module__.startswith("mk_k68_lightsmart")
        for name, value in vars(cls).items()
        if isinstance(value, Signal)
    }
    meta = page.metaObject()
    state = {}
    for index in range(meta.methodCount()):
        method = meta.method(index)
        name = bytes(method.name()).decode()
        if (method.methodType() == QMetaMethod.MethodType.Signal
                and name in names):
            state[name] = page.isSignalConnected(method)
    return state


@unittest.skipIf(QApplication is None, "PySide6 is not installed")
class TheWindowReallyConnectsThem(unittest.TestCase):
    """That ``MainWindow`` *did* connect each screen, not that it could.

    ``connects`` above asks whether a signal accepts a connection, which
    every signal does, so it passes just as happily when the window
    never mentions the screen at all. That is the hole Music's
    ``apply_effect`` fell through: pressing "Apply to keyboard" set the
    status line, lit the screen's own preview and then dropped the
    command, because Qt discards an emit with no receiver in silence.
    Nothing looked broken anywhere except on the keyboard.
    """

    PAGES = ("keys_page", "macro_page", "profiles_page",
             "custom_page", "music_page")

    def window(self):
        window = gui.MainWindow()
        # The window owns a thread that opens the device. Stopping it is
        # what lets the worker close the descriptor on the thread that
        # opened it, and keeps a torn-down window from emitting into a
        # deleted receiver.
        self.addCleanup(window.device.stop)
        return window

    def test_no_screen_signal_is_left_dangling(self):
        window = self.window()
        for name in self.PAGES:
            for signal, connected in own_signals(
                    getattr(window, name)).items():
                with self.subTest(page=name, signal=signal):
                    self.assertTrue(
                        connected,
                        f"{name}.{signal} is emitted but nothing in "
                        f"MainWindow listens, so it does nothing at all")

    def test_music_hands_its_resting_board_to_the_firmware(self):
        # The specific regression: resting lit goes out as one effect
        # command, not as 69 per-key writes, so Music needs the effect
        # handler and not only the per-key one.
        window = self.window()
        self.assertTrue(own_signals(window.music_page)["apply_effect"])


if __name__ == "__main__":
    unittest.main()
