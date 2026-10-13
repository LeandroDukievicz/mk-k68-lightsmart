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
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication
except ImportError:                                   # pragma: no cover
    QApplication = None

if QApplication is not None:
    from mk_k68_lightsmart import screens

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


if __name__ == "__main__":
    unittest.main()
