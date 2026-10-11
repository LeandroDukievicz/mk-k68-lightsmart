"""Low-level HID transport for the SONiX KT68 RGB "smart screen" keyboard.

Reverse-engineered from the Windows driver (DeviceDriver.exe v1.1, 7 Mar 2023).

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

import errno
import fcntl
import glob
import os
import struct
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


#: HIDIOCGRAWINFO -- struct hidraw_devinfo { __u32 bustype; __s16 vendor,
#: product; }. Used to re-check the device once it is open.
_HIDIOCGRAWINFO = _ioc(2, ord("H"), 0x03, 8)
#: HIDIOCGRDESCSIZE / HIDIOCGRDESC -- the report descriptor, read off the
#: open descriptor rather than from sysfs.
_HIDIOCGRDESCSIZE = _ioc(2, ord("H"), 0x01, 4)
_HID_MAX_DESCRIPTOR_SIZE = 4096
_HIDIOCGRDESC = _ioc(2, ord("H"), 0x02, 4 + _HID_MAX_DESCRIPTOR_SIZE)


class DeviceNotFound(Exception):
    """No KT68 vendor control interface is present."""


class TransportError(Exception):
    """A transfer failed or the keyboard reported an error."""


class DeviceGone(TransportError):
    """The device stopped answering -- unplugged, or reset.

    Distinct from a plain :class:`TransportError` because a protocol
    rejection means "try something else" while this means "the handle is
    dead, reconnect".
    """


#: Errnos that mean the device itself is gone, not that the command was bad.
_GONE = frozenset((
    errno.ENODEV, errno.ENXIO, errno.EIO, errno.ESHUTDOWN,
    errno.EBADF, errno.ENOENT, errno.EPIPE,
))


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
    n = len(blob)
    while i < n:
        prefix = blob[i]
        if prefix == 0xFE:  # long item, not used here
            if i + 1 >= n:
                return None
            i += 3 + blob[i + 1]
            continue
        size = (0, 1, 2, 4)[prefix & 0x03]
        if i + 1 + size > n:
            return None
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


def keyboard_present() -> bool:
    """Whether any interface of this keyboard is on the bus at all.

    Distinguishes "not plugged in, or the switch is not on wired" from
    "plugged in, but the control interface is not reachable" -- which
    are different problems with different fixes, and the only two the
    setup screen can tell apart without opening anything.
    """
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = _read_sysfs(os.path.join(node, "device", "uevent"))
        for line in uevent.splitlines():
            if not line.startswith("HID_ID="):
                continue
            parts = line.split("=", 1)[1].split(":")
            if len(parts) != 3:
                continue
            try:
                if (int(parts[1], 16), int(parts[2], 16)) == (VENDOR_ID, PRODUCT_ID):
                    return True
            except ValueError:
                continue
    return False


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
        f"{name} (usage page {ident[0]:#06x}/{ident[1]:#06x})"
        if ident else f"{name} (undecoded)"
        for name, ident in candidates
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
        except OSError as exc:
            raise TransportError(f"cannot open {self.path}: {exc}") from exc

        # find_control_device() picked this path by reading sysfs; the open
        # is a separate step, and hidraw minor numbers are reused when a
        # device is replugged. Confirm on the descriptor itself, so we can
        # never write vendor commands into some other HID device.
        try:
            self._verify_identity()
        except Exception:
            self.close()
            raise

    def _verify_identity(self) -> None:
        info = bytearray(8)
        try:
            fcntl.ioctl(self._fd, _HIDIOCGRAWINFO, info)
        except OSError as exc:
            raise TransportError(
                f"cannot identify {self.path}: {exc}"
            ) from exc
        _bustype, vendor, product = struct.unpack("=Ihh", bytes(info))
        vendor &= 0xFFFF
        product &= 0xFFFF
        if (vendor, product) != (VENDOR_ID, PRODUCT_ID):
            raise TransportError(
                f"{self.path} is {vendor:04x}:{product:04x}, not the expected "
                f"{VENDOR_ID:04x}:{PRODUCT_ID:04x} -- the device was probably "
                "replugged while we were looking at it"
            )

        # Same vendor id is not enough: the keyboard's typing interfaces
        # share it. Check the collection this descriptor actually speaks.
        ident = self._read_usage()
        if ident != (VENDOR_USAGE_PAGE, VENDOR_USAGE):
            got = "undecodable" if ident is None else (
                f"usage page {ident[0]:#06x}/{ident[1]:#06x}"
            )
            raise TransportError(
                f"{self.path} is the right device but the wrong interface "
                f"({got}, want {VENDOR_USAGE_PAGE:#06x}/{VENDOR_USAGE:#06x})"
            )

    def _read_usage(self) -> tuple[int, int] | None:
        """Parse the report descriptor straight off the open descriptor."""
        try:
            size_buf = bytearray(4)
            fcntl.ioctl(self._fd, _HIDIOCGRDESCSIZE, size_buf)
            size = struct.unpack("=I", bytes(size_buf))[0]
            if not 0 < size <= _HID_MAX_DESCRIPTOR_SIZE:
                return None
            desc = bytearray(4 + _HID_MAX_DESCRIPTOR_SIZE)
            desc[0:4] = struct.pack("=I", size)
            fcntl.ioctl(self._fd, _HIDIOCGRDESC, desc)
        except OSError as exc:
            raise TransportError(
                f"cannot read the report descriptor of {self.path}: {exc}"
            ) from exc
        return _parse_report_descriptor(bytes(desc[4 : 4 + size]))

    # -- context manager ------------------------------------------------
    def __enter__(self) -> "KT68Transport":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        fd, self._fd = getattr(self, "_fd", None), None
        if fd is not None:
            os.close(fd)

    @property
    def closed(self) -> bool:
        return getattr(self, "_fd", None) is None

    def _check_open(self) -> int:
        fd = getattr(self, "_fd", None)
        if fd is None:
            raise TransportError(f"transport for {self.path} is closed")
        return fd

    # -- raw transfers --------------------------------------------------
    def set_feature(self, payload: bytes) -> None:
        """Send one 64-byte payload as a feature report."""
        if len(payload) > REPORT_SIZE:
            raise ValueError(f"payload is {len(payload)} bytes, max {REPORT_SIZE}")
        fd = self._check_open()
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            payload = bytes(payload)
        buf = self._tx
        buf[0] = 0  # the device declares no report id
        buf[1 : 1 + len(payload)] = payload
        if len(payload) < REPORT_SIZE:
            # clear whatever the previous transfer left in the tail
            buf[1 + len(payload) :] = bytes(REPORT_SIZE - len(payload))
        assert len(buf) == WIRE_SIZE, "wire buffer changed length"
        time.sleep(self.pacing)
        try:
            fcntl.ioctl(fd, self._set_req, buf)
        except OSError as exc:
            raise self._io_error("write to", exc) from exc

    def get_feature(self) -> bytes:
        """Read one 64-byte feature report back from the keyboard."""
        fd = self._check_open()
        buf = self._rx
        buf[0] = 0
        time.sleep(self.pacing)
        try:
            fcntl.ioctl(fd, self._get_req, buf)
        except OSError as exc:
            raise self._io_error("read from", exc) from exc
        return bytes(buf[1:])

    def _io_error(self, what: str, exc: OSError) -> TransportError:
        """Turn an ioctl failure into the right exception.

        When the errno says the device is gone the handle is closed here,
        so ``closed`` means what callers assume it means and a stale fd is
        not kept alive for the rest of the session.
        """
        if exc.errno in _GONE:
            self.close()
            return DeviceGone(
                f"{what} {self.path} failed: {exc} -- the keyboard is no "
                "longer there"
            )
        return TransportError(f"{what} {self.path} failed: {exc}")

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
