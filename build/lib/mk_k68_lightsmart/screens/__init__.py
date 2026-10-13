"""The screens the rail opens, beyond Home, Light and Matrix.

Each one is modelled on the vendor's own screen -- the controls it
offers and the order it offers them in -- drawn in this app's chrome
rather than its artwork, which does not ship here.

They differ in how much of the keyboard they can actually reach, and
the difference is the protocol's, not the screen's. One module each,
so the screens stay separable.

The import runs after ``gui`` is loaded -- ``MainWindow`` imports this
package from inside its constructor -- so taking the shared chrome from
``gui`` at module level is not circular.
"""

from .custom import CustomPage
from .keys import KeysPage
from .macro import MacroPage
from .music import LevelBar, MusicPage
from .profiles import ProfilesPage

__all__ = [
    "CustomPage",
    "KeysPage",
    "LevelBar",
    "MacroPage",
    "MusicPage",
    "ProfilesPage",
]
