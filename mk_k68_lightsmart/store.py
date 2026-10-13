"""Where a configuration scheme lives, which is here and not the keyboard.

The vendor calls these configuration schemes; this app calls them the
same thing. They are a host-side bundle on purpose, not for want of a
command: reading the driver's own idle-tick dispatcher, the cases that
change scheme send the keyboard nothing at all (docs/PROTOCOL.md §10).
The app re-sends the keymap and the lighting instead. So a scheme is
whatever the host can replay, and this module is the file it replays
from.

One file, written whole. A scheme is at most a few kilobytes of JSON
and is saved when someone clicks something, so the cost of rewriting
all of it is nothing against the cost of a half-written one -- which
is why the save goes through a temporary file and a rename.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Bumped when a stored file stops being readable by this code. Nothing
#: migrates yet; a file from the future is refused rather than guessed at.
VERSION = 1


def config_dir() -> Path:
    """Where this machine keeps the file.

    ``SNAP_USER_DATA`` first: inside a snap the home directory is not
    the one the user thinks it is, and writing to ``~/.config`` from
    there lands somewhere nothing else will look.
    """
    snap = os.environ.get("SNAP_USER_DATA")
    if snap:
        return Path(snap) / "schemes"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    root = Path(xdg) if xdg else Path.home() / ".config"
    return root / "mk-k68-lightsmart"


#: The one store the app reads and writes. See :func:`shared`.
_shared: "Store | None" = None


def shared() -> "Store":
    """The store every screen works through.

    Each screen keeps a different part of a scheme, and saving writes
    the whole file. Four screens each holding their own ``Store.load()``
    would be four views of one file, and whichever saved last would
    quietly drop the other three's work. So there is exactly one, and
    screens take it from here rather than loading their own.
    """
    global _shared
    if _shared is None:
        _shared = Store.load()
    return _shared


def use(store: "Store") -> None:
    """Put a different store in place, which is how tests stay off the
    real file: build one on a temporary path and hand it over here."""
    global _shared
    _shared = store


@dataclass
class Scheme:
    """One named bundle of everything the host can put on the keyboard.

    The three parts are kept apart because they travel by different
    commands and some of them do not travel at all yet -- macros are
    stored and never sent, because the event encoding is open.
    """

    name: str = "Default"
    #: remap slot -> (usage, modifiers), exactly what ``remap_block`` takes.
    keymap: dict[int, list[int]] = field(default_factory=dict)
    #: LED index -> 0xRRGGBB, the painted layout.
    lighting: dict[int, int] = field(default_factory=dict)
    #: macro name -> list of {"kind": str, "value": str}.
    macros: dict[str, list[dict]] = field(default_factory=dict)

    def remap_changes(self) -> dict[int, tuple[int, int]]:
        """The keymap in the shape ``protocol.remap_block`` wants."""
        return {int(slot): (pair[0], pair[1] if len(pair) > 1 else 0)
                for slot, pair in self.keymap.items()}


@dataclass
class Store:
    """Every scheme, and which one is in use."""

    schemes: list[Scheme] = field(default_factory=lambda: [Scheme()])
    active: int = 0
    path: Path | None = None

    # -- the one in use -------------------------------------------------
    @property
    def current(self) -> Scheme:
        # Clamped rather than trusted: the index is in the file, and a
        # hand-edited or truncated one should not crash the app.
        if not self.schemes:
            self.schemes.append(Scheme())
        self.active = max(0, min(self.active, len(self.schemes) - 1))
        return self.schemes[self.active]

    def names(self) -> list[str]:
        return [s.name for s in self.schemes]

    def add(self, name: str) -> Scheme:
        scheme = Scheme(name=self._free_name(name))
        self.schemes.append(scheme)
        return scheme

    def duplicate(self, index: int) -> Scheme:
        source = self.schemes[index]
        copy = Scheme(
            name=self._free_name(f"{source.name} copy"),
            keymap=dict(source.keymap),
            lighting=dict(source.lighting),
            macros={k: list(v) for k, v in source.macros.items()},
        )
        self.schemes.append(copy)
        return copy

    def remove(self, index: int) -> bool:
        """Drop a scheme, unless it is the last one.

        There is always one: every screen reads ``current``, and a
        store with nothing in it would mean a null check in each.
        """
        if len(self.schemes) <= 1:
            return False
        self.schemes.pop(index)
        self.active = min(self.active, len(self.schemes) - 1)
        return True

    def _free_name(self, wanted: str) -> str:
        taken = set(self.names())
        if wanted not in taken:
            return wanted
        n = 2
        while f"{wanted} {n}" in taken:
            n += 1
        return f"{wanted} {n}"

    # -- the file -------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "Store":
        """Read the file, or hand back an empty store if there is none.

        A missing file is the ordinary first run. A corrupt one is not
        ordinary, but losing the app to it would be worse than losing
        the schemes, so it reads as empty too.
        """
        path = path or (config_dir() / "schemes.json")
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            return cls(path=path)
        if raw.get("version") != VERSION:
            return cls(path=path)
        schemes = [
            Scheme(
                name=str(s.get("name", "Scheme")),
                keymap={int(k): list(v) for k, v in (s.get("keymap") or {}).items()},
                lighting={int(k): int(v) for k, v in (s.get("lighting") or {}).items()},
                macros={str(k): list(v) for k, v in (s.get("macros") or {}).items()},
            )
            for s in raw.get("schemes", [])
        ]
        return cls(schemes=schemes or [Scheme()],
                   active=int(raw.get("active", 0)), path=path)

    def save(self) -> Path:
        """Write every scheme, atomically.

        The rename is what makes it atomic; writing in place would lose
        the lot if the process died mid-write, and these are the only
        copy of work somebody did by hand.
        """
        path = self.path or (config_dir() / "schemes.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": VERSION,
            "active": self.active,
            "schemes": [asdict(s) for s in self.schemes],
        }
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, indent=1, sort_keys=True)
            os.replace(tmp, path)
        except BaseException:
            # Leave the previous file alone; a failed save is better
            # than a truncated one.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.path = path
        return path
