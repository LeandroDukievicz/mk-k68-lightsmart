# Packaging for the Snap Store

## The confinement problem

The tool needs read/write access to the keyboard's `/dev/hidraw` node.
On a **classic** desktop no snap interface grants that:

```
$ snap interfaces | grep -E 'hidraw|raw-usb'
:hardware-observe
:hardware-random-control
:hardware-random-observe
:raw-usb
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
