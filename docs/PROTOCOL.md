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

### Bulk transfers

There are **two** bulk paths, and they do not count blocks the same way.
Getting this wrong means the firmware either waits for blocks that never
arrive or ignores the tail, so it is worth stating plainly:

| Function | Used by | Chunks sent |
|---|---|---|
| `fcn.00455E50` (`0x455FF1`) | macro upload | `(len >> 6) - 1` |
| `fcn.004561C0` (`0x456336`) | dot-matrix animation upload | `(len >> 6) + 1` |

The animation path is the `+1` one, and the count it announces
(`0x429CF3`) uses the same `+1`, so announce and stream agree.

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
| `0x15` | `0x43327C` | macro block follows; byte 8 = block count |
| `0x17` | `0x439F77` | — (byte 8 = `0x01`) |
| `0x18` | 13 call sites | begin transaction |
| `0x19` | `0x432DA4` | — |
| `0x20` | `0x42B34E` | — (byte 8 = `0x08`) |
| `0x23` | `0x430787` | — (byte 8 = `0x09`) |
| `0x27` | `0x41778E` | Fn-layer remap block (byte 8 = `0x09`) |
| `0x31` | `0x429D3E` | dot-matrix **boot** animation follows; bytes 8-9 = block count (LE16) |
| `0x33` | `0x42A0F8` | dot-matrix animation follows; bytes 8-9 = block count (LE16) |
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

**verified** framing and mode bytes, **inferred** parameter meanings.

Confirmed on hardware on 2026-10-11 at both ends of the table: `0x00`
turns the key backlight off, and `0x07` breathes in the colour it is
given. The modes between them are sequential, so the two extremes
pin the rest.

The mode bytes are not in the string table, and they are not the row
numbers either. The driver fills its effect list with
`AddString(name, mode, -1)` (`0x43BFA0`-`0x43C5FF`), taking the name
from string id 200+row and attaching the mode as the item's data; the
selection handler at `0x430020` reads it back with `GetCurSel` then
`GetItemData@HFDListBox` and stores it in the field that later becomes
byte 0 (`0x430628`).

| mode | name | | mode | name |
|---|---|---|---|---|
| `0x00` | Close Backlight | | `0x0A` | Scrolling |
| `0x01` | Static | | `0x0B` | Rolling |
| `0x02` | SingleOn | | `0x0C` | Rotating |
| `0x03` | SingleOff | | `0x0D` | Explode |
| `0x04` | Glittering | | `0x0E` | Launch |
| `0x05` | Falling | | `0x0F` | Ripples |
| `0x06` | Colourful | | `0x10` | Flowing |
| `0x07` | Breath | | `0x11` | Pulsating |
| `0x08` | Spectrum | | `0x12` | Tilt |
| `0x09` | Outward | | `0x13` | Shuttle |

So the mode is the row plus one, with Close Backlight — an off switch
rather than an animation — taking `0x00`. Strings 220 and 221
("Inwards", "Floweriness") name effects this keyboard's list never
offers; a second, shorter list at `0x43D800` holds ten of the same
entries, which is what the handler's `cmp ebx, 0x6` / `0x8` branches
are choosing between. `0x14` appears in the handler's comparisons
without a list entry, so something uses it; what, is not known.

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

**verified** geometry, **verified** upload format — confirmed on
hardware on 2026-10-11 with patterns built so that each possible mistake
would look different on the strip.

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

### Frame upload — `fcn.00429C20` (boot) and `fcn.00429FE0` (animation)

**verified** against the driver's encoder.

```
04 18                                      begin
04 31|33 .. .. .. .. .. .. <lo> <hi>       announce <blocks> 64-byte blocks
<header><frames…> AA 55                    bulk payload via fcn.004561C0
04 02                                      apply
04 F0                                      end
```

| | `0x31` boot logo | `0x33` animation |
|---|---|---|
| Frame cap | 134 (`cmovg`, `0x429CD5`) | 941 |
| Header | 4 bytes: count LE16, speed LE16 | **the same 4 bytes** |
| Count written at | `0x429D62` | `0x42A11C` |
| Speed written at | `0x429DC9` | `0x42A187` |
| First pixel offset | 4 | 4 (`0x42A190`) |

Both commands take the same header. Reading the animation's as 2 bytes
shifts every pixel two bytes early, and the firmware then takes the
first pixel's two leading channels for the speed — on the strip that
looks like a green/blue swap, which is how it was caught.

Each frame is 325 LEDs × 3 bytes = **975 bytes**, written **R, G, B**.
The driver stores the pixel's own bytes low-first (`0x42A27C`,
`0x42A284`, `0x42A290`; `0x429EBC`-`0x429ED0` in the boot path), and the
pixel it holds is a Win32 `COLORREF` — `0x00BBGGRR` — so R leads. Every
channel is pre-multiplied by `brightness / 255` (`0x429E56`, with 255.0f
at `0x59353C`); brightness reaches the encoder as `percent * 255 / 100`,
and the speed wire value is `0x66 - setting`, or `0x50` when the layer
has none.

The driver counts blocks twice and can disagree with itself. It
announces `((frames * 975 + 2) >> 6) + 1` (`0x42A095`-`0x42A0B1`) but
hands the bulk sender `frames * 975 + 4` (`0x42A2B5`), which applies
`(len >> 6) + 1` of its own — so at 4 frames, and every 64 frames after,
it announces one block fewer than it streams. This implementation uses
the transmitted length for both, so the keyboard is told exactly what it
then receives.

The terminator goes at `len(data) + 1` — the driver leaves one spare
byte — via `mov word [ebx+edx+1], 0x55AA` (`0x42A2B8`, `0x429EF5`). The
driver writes into a buffer far larger than it transmits, so for the
handful of lengths where `len % 64 >= 62` its own magic lands outside
the transmitted range; this implementation sends one extra block in that
case so the terminator survives.

### An upload cannot be abandoned

**verified** on hardware.

Between the announce and the last block, the firmware takes *every*
64-byte report as payload. There is no escape sequence: a `04 02` or a
`04 F0` sent mid-stream is consumed as pixels like anything else, and
until the announced count is met the keyboard answers `0x00` to
everything.

Measured: a 458-block upload stopped at block 128 left the device
rejecting commands. Sending `04 18` repeatedly, 322 of them came back
`0x00` — exactly the outstanding count, minus the handful of reports
already spent on the failed attempts — and the next ones came back
`0xFF`, busy, because the transaction was still open. `04 02` then
`04 F0` closed it, and the command channel returned.

So a cancelled or failed stream has to be *finished*, with blank blocks,
and then applied and ended. That costs 15 ms a block like any other, so
cancelling a long upload saves time but does not stop it dead. What the
keyboard shows afterwards is the part that arrived, which is the price
of getting the channel back.

### How it was confirmed

`mk-k68-lightsmart strip test` uploads one frame whose rows carry
different colours and whose columns ramp in brightness, with the first
LED white. A channel-order mistake recolours the rows, a row/column
mix-up turns the stripes vertical, a reversed column order runs the ramp
backwards, and a wrong block count truncates the picture.

The first pattern was ambiguous, and the ambiguity is worth recording:
its rows were red, **green, blue**, **yellow, magenta**, and those two
pairs are exactly what a green/blue swap maps onto each other — so a
channel swap and a row swap looked identical. `strip probe` separates
them: red and white rows on the left, which no green/blue swap can
touch, and pure green on the right, which it must.

### The stock animations

They ship as a **SQLite** database (`db/ledlayer_v1.data`):

```sql
t_ledlayer_data(led_id, profile, name, desc, speed, brightness, type, state)
t_led_frames(frames_id, layer_id, frameIndex, led_index, rgb_value)
t_default_led_frames(...)   -- same shape
```

`led_index` runs 0‥324 and `rgb_value` is `0x00RRGGBB` — the same form
this project's `Frame` uses, so the stock animations need no conversion
on the way in. The evidence is the palette itself: the colour that
dominates the CPU, GPU and network-speed layers is `0x001D30`, which
reads as the dark navy the app is themed in, and as a muddy brown the
other way round.

The driver's *in-memory* pixel is not that, though: the encoder writes
it low byte first and the strip shows R first, so what it holds is a
Win32 `COLORREF` (`0x00BBGGRR`). The app therefore swaps when it loads
a layer. Only the wire order matters for talking to the keyboard, and
that is R, G, B.

Layer 1 is the boot logo with exactly 134 frames, which is where the
cap comes from. The layer names are Chinese in the shipped database
(`开机logo`, `CPU使用率`, `时间显示`, …); the English names come from
`language/1033.lan`.

## 7. Key remapping

**verified** addressing and envelope, **partly decoded** records —
`fcn.00416F20`, bulk via `fcn.00455E50`.

```
04 18                               begin
04 11 .. .. .. .. .. .. 09          announce the keymap block
<694 bytes>                         the block itself
04 02                               apply
04 F0                               end
```

The block is a flat array of **4-byte records**, one per key slot, sent
as 694 bytes (`0x2B6`, pushed at `0x4171DC`) out of a 780-byte buffer
(`0x30C`, cleared at `0x416FA3`). A key's record starts at `slot * 4`
(`[ebp + ebx*4 - 0x360]` throughout the fill loop). The `AA 55`
terminator goes in at offset **574** (`0x4171D3`), past the records
rather than at the end of the payload.

`0x27` carries the Fn layer in the same shape, 684 bytes.

### Slots

A key's slot is **not** its LED index. The driver resolves it from the
HID usage with a compiled switch (`fcn.00457490`): a 236-byte case
index at `0x457A10` covering usages 4‥0xEF, and a jump table at
`0x4578A0` whose arms each return a constant.

Extracting it and laying it over the physical board shows the numbering
is the keyboard matrix in reading order, 18 slots to a row:

| row | starts at | |
|---|---|---|
| ``` ` ``` 1 2 3 … | 19 | Backspace is 103 |
| Tab Q W E … | 37 | Del 119 |
| Caps A S D … | 55 | Enter 85, PgUp 118 |
| Shift Z X C … | 73 | ↑ 101, PgDn 121 |
| Ctrl Win Alt … | 91 | arrows 99-102 |

Esc is 1 and the slider switch is 124. All 69 keys of this board land
on a slot of their own; the table is in `layout.REMAP_SLOTS`.

### Records

The action type comes from the editor's own record (`[esi+0x18]`) and
decides the shape:

| type | record | meaning |
|---|---|---|
| 1 | `05 03 00 00` | left as it is |
| 2 | `02 <mod> <usage> 00` | another key; a modifier goes in byte 1 and byte 2 is cleared (`0x41728B`) |
| 0xD | `07 <a> <b> <c>` | a three-parameter action, source fields `+0x1C`, `+0x20`, `+0x24` |
| 3 | — | a macro, looked up by id in the macro list (`0x417310`) |

Usages `0xF1`-`0xF3` and `0xD3` take their own branches (`0x4172B1`),
writing the original key's usage into byte 2 and the special id into
byte 3. Bytes 1 and 2 of an ordinary remap come out of `fcn.004588A0`,
which is not decoded yet — so **nothing here writes a keymap to the
keyboard**. A keymap is persistent, and a wrong one types the wrong
letters until it is corrected.

## 8. Macros

**partly verified** — `fcn.00432D10`, bulk via `fcn.00455E50`

```
04 15 .. .. .. .. .. .. <blocks>     announce
<encoded events> AA 55               bulk payload
04 02                                apply
```

`blocks = len/64 + (1 if len % 64 == 0 else 2)` (`0x433230`). Events are
encoded one at a time; a key event emits the marker `0x50` followed by
two bytes from the event record (`0x432F8F`). The full event encoding is
not decoded yet.

## 9. What is still open

1. ~~Numeric ids for the lighting effects~~ — recovered from the
   driver's own list population; see §5. What `0x14` is remains open.
2. Which of bytes 8-11 is brightness, speed, direction and
   random-colour, and their ranges.
3. ~~The rest of the remap record~~ — the ordinary case is decoded,
   `fcn.004588A0` included; see §7. What remains is the action types
   beyond a plain key: the three-parameter `0x0D`, and the macro
   reference.
4. The macro event encoding beyond the `0x50` key marker.
5. Meaning of sub-commands `0x17`, `0x19`, `0x20`, `0x23`, `0x35`,
   `0x61`, `0xF5`. Every control command the driver builds, counted by
   how often it writes the opcode:

   | sub | writes | known as |
   |---|---|---|
   | `0x18` | 13 | begin |
   | `0xF0` | 8 | end |
   | `0x35` | 4 | — |
   | `0x13` | 3 | select the lighting target |
   | `0x20` | 2 | — |
   | `0x02` | — | apply |
   | `0x11` `0x27` | 1 each | keymap, Fn layer |
   | `0x31` `0x33` | 1 each | boot animation, animation |
   | `0x15` | 1 | macro |
   | `0x17` `0x19` `0x23` `0x61` `0xF5` | 1 each | — |

   One of the unknown five is almost certainly the reset: the vendor UI
   offers "Reset Keyboard Data" and "Restore Factory Settings" (strings
   410 and 411), so the firmware has the command. Finding it is the
   first step towards a key-remap screen, because it is the way back
   from a keymap written wrong.

What is left is quickest to settle by sending a packet and watching the
keyboard, which is why the CLI exposes a raw send path.
