# Packaging for the Snap Store

## The confinement problem

The tool needs read/write access to the keyboard's `/dev/hidraw` node.
On a **classic** desktop no snap interface grants that:

```
$ snap interface hidraw
name:          hidraw
summary:       allows access to specific hidraw device
documentation: https://snapcraft.io/docs/hidraw-interface
          <- no "slots:" section: nothing on this system provides one

$ snap interfaces | grep -c hidraw
0
```

There is no `:hidraw` system slot. snapd's `hidraw` interface declares
`slot-snap-type: [core, gadget]`, and in practice the slots come from a
gadget snap — that is, Ubuntu Core, not a desktop.

Three ways out, in order of how well they actually work:

### 1. Classic confinement (what `snapcraft.yaml` currently does)

Full host access, so `/dev/hidraw3` plus the udev rule just work.

* Needs **manual review** by the Snap Store, with a justification. Direct
  hardware access that no interface provides is an accepted reason.
* Reviewers will ask why `raw-usb` is not enough; the answer is below.

### 2. Strict confinement with `raw-usb` and libusb

`raw-usb` *is* an implicit system slot, so this would be strict
confinement with a one-off `snap connect`. The same transfers are
expressible as libusb control requests:

```
SET_REPORT  bmRequestType=0x21 bRequest=0x09 wValue=0x0300 wIndex=2  (64 bytes)
GET_REPORT  bmRequestType=0xA1 bRequest=0x01 wValue=0x0300 wIndex=2  (64 bytes)
```

The catch: the kernel rejects interface-recipient control transfers from
usbfs unless the caller has claimed the interface, and `usbhid` already
holds interface 2. So the tool would have to detach the kernel driver,
which kills the hidraw node and the keyboard's event channel for as long
as it is connected, and makes every operation a detach/reattach cycle.
Workable, but fragile for a GUI that stays open.

### 3. `custom-device` interface

snapd 2.59+ lets an app snap slot `custom-device` and name device nodes
itself. That gives strict confinement, but installation is gated behind a
store-granted snap declaration — so it is still a review conversation,
just a different one.

**Recommendation:** ship classic for now (option 1) and keep a plain
`pip`/distro install path, which is what most keyboard-control tools do.
Revisit option 3 once the GUI is stable.

## Building it

```sh
snapcraft pack                 # needs LXD
sudo snap install --dangerous --classic ./mk-k68-lightsmart_*.snap
```

Five things went wrong the first time this was built and installed, and
four of them pack cleanly and only fail when run. They are worth
knowing because every one of them will come back if the part is edited.

**Qt's libraries have to be in the snap.** patchelf — which a classic
snap needs, or its binaries keep build-time rpaths — also points every
binary at the `core24` loader, and that loader searches the base snap,
not the host. The host having `libGL.so.1` is irrelevant: importing
`PySide6.QtGui` fails until `libgl1`, `libegl1`, `libx11-6`,
`libfontconfig1`, the `xcb` family and the Wayland libraries are in
`stage-packages`. That is most of the snap's 136 MB.

**The venv records the build container's interpreter.** The python
plugin builds in `/root/parts/tool/install`, so `pyvenv.cfg` says
`home = /usr/bin` — a path that on the user's machine is either absent
or a different Python entirely. `override-prime` rewrites it to the
interpreter the snap ships.

**The shebang has to name the venv's own `bin/python3`,** not the
interpreter that symlink points at. Python reads `pyvenv.cfg`, and so
finds `site-packages`, only when the executable it was *started as*
lives inside the venv. Naming `usr/bin/python3.12` builds, installs,
and then cannot import the application.

**A desktop file in `snap/gui/` is installed twice.** Snapcraft copies
everything under `snap/gui/` into `meta/gui`, and the app's `desktop:`
key installs its own copy, so the app appears in the menu twice. The
file lives in `desktop/` instead, and only the `desktop:` key ships it.

**Rebuilds need cleaning.** Editing the part re-runs its build over the
install directory the last one left, and `python -m venv` will not
build a venv on top of a venv:

```sh
snapcraft clean tool && snapcraft pack     # after editing the part
snapcraft clean && snapcraft pack          # after changing what ships
```

The second form matters because `prime` is not rebuilt from scratch
otherwise: a file removed from the project stays in the snap, which is
how the duplicate desktop entry survived its own fix.

## Branding and artwork

The vendor UI is covered in the manufacturer's name and logo, and the
skin PNGs under `reference/` are their copyrighted artwork. Reproducing
the **layout and behaviour** is fine; redistributing their images, logo
and wordmark in a published snap is not.

So the published package keeps the screens, control positions and
behaviour identical, and replaces:

* the top-bar wordmark and the sidebar logo,
* the keyboard render (the stock one has the brand on the spacebar),
* the installer/app icon.

The string tables are a grey area too — the English text is short and
largely functional ("Brightness", "Speed"), but the published build
should carry its own translations rather than ship `1033.lan` verbatim.

### The shipped effects are the exception, deliberately

`mk_k68_lightsmart/effects/` holds eighteen animations converted from
the manufacturer's own online library, and the published snap carries
them. That is a decision, not an oversight: without them the strip
editor opens on something the owner has to go and find, and the point
of the package is to work on install.

What it costs is the argument above. These are the manufacturer's
designs, and several of them draw characters owned by others again, so
this is the one part of the package that could reasonably be asked for
by name. They are kept apart from everything else for exactly that
reason: one directory, regenerated by `tools/convert_effects.py`, and
nothing else depends on it -- `patterns.bundled()` returns empty and the
app offers only its own five animations when the directory is not
there.
