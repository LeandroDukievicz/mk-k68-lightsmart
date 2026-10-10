# MK-K68-LightSmart

Linux control panel for 68-key mechanical keyboards that carry a
**65x5 dot-matrix LED strip** above the function row — the hardware whose
vendor software only ships for Windows.

The keyboard's configuration protocol was reverse-engineered from the
Windows driver; nothing here touches firmware. See
[docs/PROTOCOL.md](docs/PROTOCOL.md) for the full write-up, including
which parts are verified and which are still inferred.

## Status

Early. The transport layer is complete and proven against the real
device; the GUI is not built yet.

| Area | State |
|---|---|
| Device discovery (by usage page, not by guessing hidraw numbers) | done |
| Feature-report transport, 15 ms pacing, ack handling | done |
| Transaction envelope (begin / select / apply / end) | done |
| Key table with LED indices, all 69 keys | done |
| Per-key colour command | built, awaiting hardware confirmation |
| Built-in animations | framing known, effect ids not yet |
| Dot-matrix strip upload | geometry known, format not yet |
| Key remap, macros | opcodes located, payloads not decoded |
| GUI | not started |

## Requirements

* The keyboard connected **by cable**, slide switch in wired mode. The
  vendor interface is not exposed over the wireless link.
* Python 3.10+.

## Install the udev rule

Without it the tool needs root.

```sh
sudo cp udev/60-mk-k68-lightsmart.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=hidraw
```

The `60-` prefix matters: the rule tags the device `uaccess`, and the
builtin that acts on that tag runs from `70-uaccess.rules`.

## Use

```sh
python3 -m mk_k68_lightsmart.cli info     # what was detected (read-only)
python3 -m mk_k68_lightsmart.cli keys     # every key and its LED index
python3 -m mk_k68_lightsmart.cli raw "04 18" --read
python3 -m mk_k68_lightsmart.cli key Esc FF0000
```

`raw` is the bring-up tool: it sends one payload and optionally reads the
reply, which is how the remaining unknowns in the protocol get settled.

## Layout

```
mk_k68_lightsmart/
    transport.py   HID feature-report channel, device discovery
    protocol.py    command framing and the transaction envelope
    layout.py      the 69 keys and their LED indices
    cli.py         bring-up front end
docs/PROTOCOL.md   the reverse-engineering write-up
udev/              access rule
reference/         vendor material, local only, never shipped
```
