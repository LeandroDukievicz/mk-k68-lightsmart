"""Low-level HID transport for the SONiX KT68 RGB "smart screen" keyboard.

Reverse-engineered from the Windows driver (DeviceDriver.exe V1.1, Nov 2022).

The keyboard exposes three USB HID interfaces:

  * interface 0 - boot keyboard
  * interface 1 - mouse / consumer / NKRO keyboard, plus a vendor
    top-level collection (usage page 0xFFFF, usage 0x01, report id 5,
    3 bytes IN) used by the firmware to push events to the host
  * interface 2 - vendor control channel, usage page 0xFF13, usage 0x01,
    64-byte Input/Output/Feature reports and no report id

All configuration travels over interface 2 as 64-byte HID *feature*
reports.  The Windows driver statically links hidapi and calls
``HidD_SetFeature`` / ``IOCTL_HID_GET_FEATURE``; the Linux equivalents are
the HIDIOCSFEATURE / HIDIOCGFEATURE ioctls on the matching /dev/hidraw
node.  Because the device declares no report id, the report is 65 bytes on
the wire: a leading 0x00 followed by the 64 payload bytes.
"""

from __future__ import annotations

import fcntl
import glob
import os
import time

VENDOR_ID = 0x05AC          # the keyboard borrows Apple's vendor id
PRODUCT_ID = 0x024F
VENDOR_USAGE_PAGE = 0xFF13  # interface 2 top-level collection
VENDOR_USAGE = 0x0001

REPORT_SIZE = 64            # payload bytes per feature report
WIRE_SIZE = REPORT_SIZE + 1 # payload plus the leading report-id byte

#: The driver sleeps 15 ms around every transfer.  Going faster makes the
#: firmware drop packets, so the pacing is part of the protocol.
PACING = 0.015

#: Offset inside a reply that carries the status code.
ACK_OFFSET = 3
ACK_OK = 0x01
ACK_RETRY = 0xFF


def _ioc(direction: int, typ: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (typ << 8) | nr


def _hidiocsfeature(size: int) -> int:
    return _ioc(3, ord("H"), 0x06, size)


def _hidiocgfeature(size: int) -> int:
    return _ioc(3, ord("H"), 0x07, size)


class DeviceNotFound(Exception):
    """No KT68 vendor control interface is present."""


class TransportError(Exception):
    """A transfer failed or the keyboard reported an error."""


def _read_sysfs(path: str) -> str:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _parse_report_descriptor(blob: bytes) -> tuple[int, int] | None:
    """Return (usage_page, usage) of the first top-level collection.

    Only the handful of short items that can precede the opening
    Collection are decoded, which is all these keyboards emit.
    """
    usage_page = usage = None
    i = 0
    while i < len(blob):
        prefix = blob[i]
        if prefix == 0xFE:  # long item, not used here
            size = blob[i + 1]
            i += 3 + size
            continue
        size = {0: 0, 1: 1, 2: 2, 3: 4}[prefix & 0x03]
        tag = prefix & 0xFC
        data = int.from_bytes(blob[i + 1 : i + 1 + size], "little")
        if tag == 0x04:      # Global: Usage Page
            usage_page = data
        elif tag == 0x08:    # Local: Usage
            if usage is None:
                usage = data
        elif tag == 0xA0:    # Main: Collection
            if usage_page is not None and usage is not None:
                return usage_page, usage
            return None
        i += 1 + size
    return None


def find_control_device() -> str:
    """Locate the /dev/hidraw node for the vendor control interface.

    The node is identified the same way the Windows driver identifies it:
    by vendor/product id plus the usage page and usage of the interface's
    top-level collection.
    """
    candidates = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.join(node, "device")
        uevent = _read_sysfs(os.path.join(dev, "uevent"))
        hid_id = ""
        for line in uevent.splitlines():
            if line.startswith("HID_ID="):
                hid_id = line.split("=", 1)[1]
        if not hid_id:
            continue
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        try:
            vid, pid = int(parts[1], 16), int(parts[2], 16)
        except ValueError:
            continue
        if (vid, pid) != (VENDOR_ID, PRODUCT_ID):
            continue
        try:
            with open(os.path.join(dev, "report_descriptor"), "rb") as fh:
                desc = fh.read()
        except OSError:
            continue
        ident = _parse_report_descriptor(desc)
        candidates.append((os.path.basename(node), ident))
        if ident == (VENDOR_USAGE_PAGE, VENDOR_USAGE):
            return "/dev/" + os.path.basename(node)

    if not candidates:
        raise DeviceNotFound(
            f"no HID interface with id {VENDOR_ID:04x}:{PRODUCT_ID:04x} found - "
            "is the keyboard connected by cable and the switch set to wired mode?"
        )
    seen = ", ".join(
        f"{name} (usage page {p:#06x}/{u:#06x})" if i else f"{name} (undecoded)"
        for name, i in candidates
        for p, u in [i or (0, 0)]
    )
    raise DeviceNotFound(
        "the keyboard was found but its vendor control interface "
        f"(usage page {VENDOR_USAGE_PAGE:#06x}) was not. Interfaces seen: {seen}"
    )


class KT68Transport:
    """Feature-report channel to the keyboard's vendor interface."""

    def __init__(self, path: str | None = None, pacing: float = PACING):
        self.path = path or find_control_device()
        self.pacing = pacing
        # A bulk upload is tens of thousands of transfers, so the wire
        # buffers are allocated once and reused rather than per call.
        self._tx = bytearray(WIRE_SIZE)
        self._rx = bytearray(WIRE_SIZE)
        self._set_req = _hidiocsfeature(WIRE_SIZE)
        self._get_req = _hidiocgfeature(WIRE_SIZE)
        try:
            self._fd = os.open(self.path, os.O_RDWR)
        except PermissionError as exc:
            raise TransportError(
                f"cannot open {self.path}: {exc}. Install the udev rule "
                "(see udev/60-mk-k68-lightsmart.rules) or run as root."
            ) from exc

    # -- context manager ------------------------------------------------
    def __enter__(self) -> "KT68Transport":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if getattr(self, "_fd", None) is not None:
            os.close(self._fd)
            self._fd = None

    # -- raw transfers --------------------------------------------------
    def set_feature(self, payload: bytes) -> None:
        """Send one 64-byte payload as a feature report."""
        if len(payload) > REPORT_SIZE:
            raise ValueError(f"payload is {len(payload)} bytes, max {REPORT_SIZE}")
        buf = self._tx
        buf[0] = 0  # the device declares no report id
        buf[1 : 1 + len(payload)] = payload
        if len(payload) < REPORT_SIZE:
            # clear whatever the previous transfer left in the tail
            buf[1 + len(payload) :] = bytes(REPORT_SIZE - len(payload))
        time.sleep(self.pacing)
        fcntl.ioctl(self._fd, self._set_req, buf)

    def get_feature(self) -> bytes:
        """Read one 64-byte feature report back from the keyboard."""
        buf = self._rx
        buf[0] = 0
        time.sleep(self.pacing)
        fcntl.ioctl(self._fd, self._get_req, buf)
        return bytes(buf[1:])

    def send_bulk(self, payload: bytes) -> int:
        """Send a payload longer than one report, 64 bytes at a time.

        The driver splits bulk transfers the same way and paces each
        chunk, which is why this does not try to go faster. Returns the
        number of chunks sent.
        """
        if len(payload) % REPORT_SIZE:
            raise ValueError(
                f"bulk payload must be a multiple of {REPORT_SIZE} bytes, "
                f"got {len(payload)}"
            )
        chunks = len(payload) // REPORT_SIZE
        for i in range(chunks):
            self.set_feature(payload[i * REPORT_SIZE : (i + 1) * REPORT_SIZE])
        return chunks
