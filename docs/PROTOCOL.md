# Vendor HID protocol

Recovered from the Windows driver `DeviceDriver.exe` v1.1 (7 Mar 2023),
an MFC application that statically links hidapi. Addresses below are
file-relative virtual addresses in that binary, so every claim can be
re-checked.

Status of each section is marked **verified** (read directly out of the
disassembly or off the live device) or **inferred** (consistent with the
code but not yet confirmed against hardware).

## 1. Device

**verified**

| | |
|---|---|
| USB id | `05AC:024F` — the board borrows Apple's vendor id |
| Manufacturer / product | `SONiX` / `KT68 RGB` |
| Interfaces | 3 |

The firmware registers itself in the driver as the string `KT68` with
VID `0x5AC` / PID `0x24F` (`0x41ACA2`).

| Interface | Class | Purpose |
|---|---|---|
| 0 | HID boot keyboard | normal typing |
| 1 | HID mouse/consumer | media keys, NKRO, mouse; also carries a vendor top-level collection (usage page `0xFFFF`, usage `0x01`, report id 5, 3 bytes IN) the firmware uses to push events to the host |
| 2 | HID vendor | **the configuration channel** — usage page `0xFF13`, usage `0x01` |

Interface 2's report descriptor:

```
06 13 FF   Usage Page (Vendor 0xFF13)
09 01      Usage (1)
A1 01      Collection (Application)
15 00        Logical Minimum (0)
26 FF 00     Logical Maximum (255)
75 08        Report Size (8)
95 40        Report Count (64)
09 02 81 02    Input   (64 bytes)
09 03 91 02    Output  (64 bytes)
0A 00 FF B1 02 Feature (64 bytes)
C0         End Collection
```

No report id is declared, and there is no OUT endpoint — so output and
feature reports both travel over the control pipe.

The vendor interface is only present over USB. The manual is explicit
that configuration needs the slide switch in wired mode.

## 2. Transport

**verified** — `fcn.00455E50`

The driver sends every command as a **64-byte HID feature report**. On
Windows that is `HidD_SetFeature` (resolved by `GetProcAddress` at
`0x456E5E`, called at `0x455F08`); reads are `DeviceIoControl` with
`IOCTL_HID_GET_FEATURE` = `0x000B0192` (`0x45742C`).

Because the device declares no report id, the wire buffer is **65 bytes**:
a leading `0x00` followed by the 64 payload bytes. The driver memsets 65
bytes and copies the caller's 64 bytes to offset 1 (`0x455ECE`-`0x455F04`).

The Linux equivalents on the matching `/dev/hidraw` node:

| Windows | Linux |
|---|---|
| `HidD_SetFeature(h, buf, 65)` | `ioctl(fd, HIDIOCSFEATURE(65), buf)` |
| `IOCTL_HID_GET_FEATURE` | `ioctl(fd, HIDIOCGFEATURE(65), buf)` |

### Pacing and acknowledgement

`Sleep(15)` surrounds every transfer (`0x455EB6`, `0x455F3F`,
`0x455FAB`). The delay is part of the protocol, not politeness — the
firmware drops packets without it.

When a step asks for acknowledgement the driver reads a feature report
back and inspects **byte 3** of the payload:

| value | meaning |
|---|---|
| `0x01` | success |
| `0xFF` | busy — the driver resends once and reads again |

Payloads longer than 65 bytes are split into 64-byte chunks
(`0x455FF1`: `chunks = (len >> 6) - 1`).

## 3. Framing

**verified**

Two payload shapes occur.

### Control commands

```
byte 0:  0x04
byte 1:  sub-command
byte 8:  count or selector (command-dependent)
rest:    zero
```

Sub-commands seen in the binary:

| sub | where | meaning |
|---|---|---|
| `0x02` | 16 call sites | apply / flush |
| `0x11` | `0x416F9E` | key remap block follows (byte 8 = `0x09`) |
| `0x13` | `0x4305EC` | select lighting target (byte 8 = `0x01`) |
| `0x15` | `0x43327C` | — |
| `0x17` | `0x439F77` | — (byte 8 = `0x01`) |
| `0x18` | 13 call sites | begin transaction |
| `0x19` | `0x432DA4` | — |
| `0x20` | `0x42B34E` | — (byte 8 = `0x08`) |
| `0x23` | `0x430787` | — (byte 8 = `0x09`) |
| `0x27` | `0x41778E` | Fn-layer remap block (byte 8 = `0x09`) |
| `0x31` | `0x429D3E` | macro-related |
| `0x33` | `0x42A0F8` | macro-related |
| `0x35` | `0x42A41F` | — (byte 8 = `0x10`) |
| `0x61` | `0x428B62` | — |
| `0xF0` | 8 call sites | end transaction |
| `0xF5` | `0x430BCE` | — (byte 8 = `0x09`) |

### Data frames

16 bytes, terminated by the magic `AA 55`. A 64-byte report can carry
four of them back to back (`0x43A0BC` builds one with the magic at
offsets 62-63).

```
byte 0:      mode
bytes 1-3:   R, G, B
bytes 4-7:   zero
bytes 8-11:  parameters
bytes 12-13: zero
bytes 14-15: AA 55
```

## 4. Transaction envelope

**verified** — identical in `fcn.00430580` and `fcn.004309D0`

The driver never sends a data frame bare:

```
04 18                          begin            (ack)
04 13 .. .. .. .. .. .. 01     select lighting  (ack)
<data frame>                                    (no ack)
04 02                          apply            (ack)
04 F0                          end              (ack)
```

## 5. Lighting commands

### Built-in animation — `fcn.00430580`

**verified** framing, **inferred** parameter meanings.

Byte 0 is the effect mode, taken from the app's current-mode field
(`[esi+0x814]`). Bytes 1-3 and 8-11 are copied out of a 20-byte
per-effect settings record:

| frame byte | record offset |
|---|---|
| 1, 2, 3 (R, G, B) | 14, 15, 16 |
| 8 | 13 |
| 9 | 18 |
| 10 | 19 |
| 11 | 17 |

The UI exposes exactly four knobs for an effect — random colour,
brightness, speed and direction (strings 260-263 and 284) — which is
almost certainly what bytes 8-11 carry, but the mapping is not yet
confirmed.

The effect names come from the driver's English string table, ids
200-221:

```
200 Static        207 Spectrum     214 Ripples
201 SingleOn      208 Outward      215 Flowing
202 SingleOff     209 Scrolling    216 Pulsating
203 Glittering    210 Rolling      217 Tilt
204 Falling       211 Rotating     218 Shuttle
205 Colourful     212 Explode      219 Close Backlight
206 Breath        213 Launch       220 Inwards
                                   221 Floweriness
```

The numeric mode for each name is **not** in the string table and is the
main thing still to pin down.

### Single key colour — `fcn.004309D0`

**verified** framing.

```
80 R G B 00 00 00 00 <light_index> 00 0F 0F 00 00 AA 55
```

Mode `0x80` sets one key to a fixed colour. One key per transaction —
that is the firmware's limit, and the manual states it ("a single apply
can only set one key").

`light_index` values come from `KeyboardLayout.xml`; they run 1-124 over
69 keys and are reproduced in `mk_k68_lightsmart/layout.py`.

## 6. Dot-matrix strip

**verified** geometry, **inferred** upload format.

The strip is **65 columns x 5 rows = 325 LEDs**, measured off the
editor screenshot in the manual (24 px pitch, 65 cells across, 5 down).
That matches the manual's advice to import GIFs at 650x50 — ten pixels
per dot.

Limits stated by the driver's string table:

* animation: up to **941 frames** (string 993)
* boot animation: up to **134 frames** (string 992)
* sedentary reminder: off / 30 / 45 / 60 / 90 min (strings 982-986)

Built-in strip effects (strings 860-871): Boot Logo, CPU Usage, GPU
Usage, Internet Speed, Datatime, Sedentary Reminder, Musical, Sound
Spectrum, Tetris, Rain 1, Color, Rain 2.

`fcn.00430DA0` emits a run of frames with a loop counter in byte 0:

```
<n> FF 00 00 00 00 00 00 01 10 0C 00 00 00 AA 55
```

wrapped in the usual envelope. The per-frame pixel upload itself has not
been decoded yet.

## 7. What is still open

1. Numeric ids for the 22 lighting effects.
2. Which of bytes 8-11 is brightness, speed, direction, random-colour,
   and their ranges.
3. The dot-matrix frame upload format and its chunking.
4. Key remap and macro payload layouts (`0x11`, `0x27`, `0x31`, `0x33`).
5. Meaning of sub-commands `0x15`, `0x17`, `0x19`, `0x20`, `0x23`,
   `0x35`, `0x61`, `0xF5`.

All five are quickest to settle by sending a packet and watching the
keyboard, which is why the CLI exposes a raw send path.
