# MK-K68-LightSmart — uso local no Linux

Projeto privado, mantido e executado neste computador Linux. Não há
pacote de publicação nem suporte planejado para outros sistemas. O código
fica versionado no Git local para registrar alterações e permitir voltar
a versões anteriores.

Linux control panel for 68-key mechanical keyboards that carry a **65×5
dot-matrix LED strip** above the function row — the hardware whose vendor
software only ships for Windows.

![The main screen](docs/images/02-home-lit.png)

Nothing here touches firmware. The keyboard's own configuration
protocol was reverse-engineered from the Windows driver and is spoken
over the same vendor HID interface the Windows app uses. The full
write-up, including exactly which parts are proven and which are still
inferred, is in **[docs/PROTOCOL.md](docs/PROTOCOL.md)**.

---

## The hardware

| | |
|---|---|
| USB id | `05AC:024F` — the board borrows Apple's vendor id |
| Strings | manufacturer `SONiX`, product `KT68 RGB` |
| Keys | 68 keys + a slider switch, 69 addressable LEDs |
| Strip | 65 columns × 5 rows = **325 RGB LEDs** |
| Animation limits | 941 frames, or 134 for the boot animation |
| Connection | **USB cable only** — the vendor interface is not exposed over wireless |

The keyboard presents three HID interfaces. Typing goes through the
first two; all configuration goes through the third:

| Interface | Usage page | Role |
|---|---|---|
| 0 | `0x0001` / `0x06` | boot keyboard |
| 1 | several | mouse, media keys, NKRO, plus a vendor collection (`0xFFFF`) the firmware uses to push events to the host |
| 2 | **`0xFF13`** | **the configuration channel** |

---

## Status

The transport is complete, and both lighting paths are confirmed on
real hardware: per-key colour, and the dot-matrix upload. Every section
of the rail now opens on a screen rather than a placeholder, but they
do not all reach the keyboard — three of them edit state the protocol
cannot yet carry, and each says so on its face.

Each screen is modelled on the vendor's own, section by section, from
the handbook that ships with the Windows driver: the same regions, the
same controls, in the same order. Where this app's protocol knowledge
falls short of the vendor's, the control is still there and the screen
says what it cannot do rather than leaving the section out.

| Area | State |
|---|---|
| Device discovery by usage page, re-checked on the open fd | ✅ done |
| Feature-report transport, 15 ms pacing, ack handling | ✅ done |
| Transaction envelope | ✅ done |
| Key table, all 69 LED indices | ✅ done |
| Per-key colour | ✅ implemented, confirmed on hardware |
| Built-in animations | ✅ 20 mode bytes, confirmed on hardware; parameters still inferred |
| Dot-matrix frame upload | ✅ confirmed on hardware |
| Dot-matrix editor UI | ✅ frames, timeline, playback, import, upload |
| Key remapping | ✅ the vendor's three regions: hover panel, right-click custom-function dialog over its five key types, and the quick-select grid. Writes, with the vendor's own default map as the way back; not yet confirmed on hardware |
| User lighting | ✅ the vendor's User Light screen: scheme list, select-all / numbers / WASD, colour wheel with its fourteen presets, apply and reset. Confirmed on hardware |
| Custom lighting | ✅ the vendor's Custom Light screen: an effect per picked key, with colour and animation speed. No command puts an effect on part of the board, so it is played from the host and the screen says so |
| Music-reactive | ✅ the vendor's four knobs — visualisation mode, amplitude, background mode, background brightness. Records what is playing (`pw-record`, falling back to `parec`); degrades to a stand-in meter where neither exists |
| Macros | 🔍 the vendor's four regions, including mouse buttons and the wheel, insert-before/after, copy-paste between schemes and the three delay policies. The send is disabled: an event on the wire is a `0x50` marker and two undecoded bytes |

---

## Preparar neste Ubuntu

Este checkout foi ajustado para o Ubuntu 26.04 deste computador. O
protocolo depende de `fcntl` e do dispositivo `/dev/hidraw`; não funciona
no Windows ou macOS.

Instale as dependências do sistema:

```sh
sudo apt install python3 python3-pyside6.qtwidgets python3-pyside6.qtgui python3-pyside6.qtcore
```

O medidor de música usa PipeWire. Neste Linux ele está disponível por
`pw-record` (pacote `pipewire-bin`); sem captura de áudio, a tela usa um
medidor de demonstração.

Execute direto do checkout, sem publicar ou gerar pacote:

```sh
./mk-k68-lightsmart-gui
./mk-k68-lightsmart --version
./mk-k68-lightsmart info
```

Para abrir pelo menu de aplicativos, instale o atalho e o ícone na sua
conta. Os dois ficam fora do checkout de propósito, apontando para um
lançador em `~/.local/bin`, para o menu não quebrar se a pasta do
projeto for movida ou reconstruída:

```sh
mkdir -p ~/.local/bin ~/.local/share/applications \
         ~/.local/share/icons/hicolor/64x64/apps

cat > ~/.local/bin/mk-k68-lightsmart-gui <<EOF
#!/usr/bin/env bash
exec python3 -c "
import sys
sys.path.insert(0, '$PWD')
from mk_k68_lightsmart.gui import main
raise SystemExit(main())
" "\$@"
EOF
sed 's|gui import main|cli import main|' ~/.local/bin/mk-k68-lightsmart-gui \
    > ~/.local/bin/mk-k68-lightsmart
chmod +x ~/.local/bin/mk-k68-lightsmart ~/.local/bin/mk-k68-lightsmart-gui

cp docs/images/icon-64.png \
   ~/.local/share/icons/hicolor/64x64/apps/mk-k68-lightsmart.png
sed -e "s|^Exec=.*|Exec=$HOME/.local/bin/mk-k68-lightsmart-gui|" \
    -e 's|^Icon=.*|Icon=mk-k68-lightsmart|' \
    desktop/mk-k68-lightsmart.desktop \
    > ~/.local/share/applications/mk-k68-lightsmart.desktop
update-desktop-database ~/.local/share/applications
```

O nome do arquivo instalado importa: no Wayland a janela se identifica
pelo id que `setDesktopFileName` registra -- `mk-k68-lightsmart` -- e o
GNOME procura um `.desktop` com exatamente esse nome. Instalado como
`mk-k68-lightsmart_gui.desktop`, a busca falha, a janela não é ligada ao
item de menu que a abriu, e clicar no menu parece não fazer nada: a
janela abre sem foco, atrás do que estava em uso, com o ícone genérico.

**Não instale isto como Snap.** Houve um pacote Snap deste app, e
tê-lo instalado junto com o checkout significava duas cópias com código
diferente disputando o mesmo teclado -- uma sempre desatualizada, e
nenhuma pista de qual estava rodando. O empacotamento foi removido do
projeto. Se ainda houver um instalado de antes:

```sh
sudo snap remove mk-k68-lightsmart
```

Os esquemas ficam em `~/.config/mk-k68-lightsmart/`, fora de qualquer
pacote, então remover o Snap não os apaga.

### Manter as versões no Git

As mudanças permanecem na branch local atual. Para conferir o histórico e
salvar um marco local:

```sh
git status
git add -A
git commit -m "Melhora o controle local do teclado"
git tag v0.1.0-local
```

Não use `git push` para manter este projeto somente neste computador.

## Acesso ao teclado

Instale a regra udev para liberar somente a interface de configuração do
teclado para sua sessão, sem expor as interfaces de digitação:

Without it the tool needs root.

```sh
sudo cp udev/60-mk-k68-lightsmart.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --action=add --subsystem-match=hidraw
```

The `60-` prefix is load-bearing. The rule tags the device `uaccess`,
and the builtin that acts on that tag runs from `70-uaccess.rules` — a
`99-` rule would sort *after* it and never take effect.

The rule is scoped to **interface 2 alone**. That matters: interfaces 0
and 1 are the ones that carry the keys you type, so a rule matching only
vendor/product would hand every keystroke to any process running as the
logged-in user. This keyboard also reports Apple's `05ac:024f`, so a
broad rule would expose a genuine Apple keyboard on the same machine.

`ATTRS{bInterfaceNumber}` cannot be combined with `ATTRS{idVendor}` --
udev requires all `ATTRS` matches to land on the same parent device, and
those two live on different ones -- so the rule pins the interface with
`DEVPATH`. To check it took effect:

```sh
for n in /dev/hidraw*; do printf '%s ' "$n"; getfacl -p "$n" | grep -c "^user:$USER:"; done
```

Only the vendor interface should report `1`.

---

## The GUI

![As it opens](docs/images/01-home.png)

The screen structure mirrors the Windows app: a left rail of eight
sections plus settings, a top bar, the keyboard with its strip, and a
controls row.

**What works now, on the Home screen:**

- Click the colour swatch, then click any key — the key lights on the
  keyboard and the command goes out immediately.
- The bottom-right corner shows the exact 16 bytes that were built, so
  you can watch the protocol as you click.
- The status bar reports the device node, or why it could not be found.
- Click or drag across the strip to paint it, set a brightness, and
  press Upload. A progress bar follows the blocks.
- Cancel stops the picture, but not the transfer: the firmware counts
  the blocks it was promised and ignores the host until it has them
  all, so the rest go out blank. The status bar says how many are left
  and how long that is.
- Every transfer runs on a thread of its own, so the window stays live
  through an upload that can take minutes.

### The Matrix screen

![The strip editor](docs/images/03-matrix.png)

The strip's own screen. Frames are listed down the left; the selected
one is the picture you paint on, and the whole list goes to the keyboard
as one animation — up to the firmware's 941.

- **Add**, **Duplicate**, **Delete**, **Clear this frame**.
- **Play** previews the animation in the window at the rate the slider
  asks the keyboard for. Loading a shipped effect, or importing a GIF,
  moves the slider to the delay that source was drawn at: a 100 ms GIF
  plays at 100 ms, and one thinned to every third frame asks for 300,
  since each frame kept has to cover the two dropped beside it. When a
  source wants longer than the scale reaches, the status line says so
  rather than letting the strip run fast without explanation.
- **The rate wants measuring once per keyboard.** Everything the
  program says about frame rate comes from one number — milliseconds
  per unit of the speed byte — and it was originally eyeballed rather
  than measured. The guess was 1.5 ms; measured here it is **12.1 ms**,
  eight times slower, which is why the strip stepped frame by frame
  where the preview was fluid. Run it once:

  ```sh
  mk-k68-lightsmart calibrate-speed --save
  ```

  The strip flashes once per loop; watch it, time a few flashes, and
  type the seconds between them. Until a reading exists the slider's
  label carries a `~`, because the number it shows is a guess. The
  usable scale on this firmware is 24 ms to 1.2 s a frame, in steps of
  about 12 ms.
- **Upload animation** streams the whole thing, with the same progress
  bar and Cancel as the Home screen. 59 frames is 899 blocks, about 14
  seconds.
- **Import from a vendor database…** reads a `ledlayer_v1.data` you
  still have from the Windows installer and loads one of its layers.
  Nothing of the sort ships here.

---

## CLI

Useful for protocol work, since it sends one packet at a time.

```sh
# what was detected, read-only
./mk-k68-lightsmart info

# every key and the LED index the firmware uses for it
./mk-k68-lightsmart keys

# light one key
./mk-k68-lightsmart key Esc FF0000

# free a keyboard that stopped answering, which is what an upload
# interrupted partway leaves behind. The app does this by itself.
./mk-k68-lightsmart unstick

# list the built-in animations and the mode byte each one uses
./mk-k68-lightsmart effects

# select one, by name or by mode
./mk-k68-lightsmart effect Breath 00FFFF --brightness 12 --speed 8

# upload one frame to the strip: a pattern built to expose mistakes
./mk-k68-lightsmart strip test
./mk-k68-lightsmart strip fill 00FF00 --brightness 40
./mk-k68-lightsmart strip clear

# remap a key, and put the vendor's own default map back. This one
# persists across a power cycle, so it asks before it writes.
./mk-k68-lightsmart remap --set Caps=0xe0 --dry-run
./mk-k68-lightsmart remap --defaults

# walk the effect modes, recording what each one does
./mk-k68-lightsmart effect-scan

# cycle Claude 5h, Claude 7d, Codex 5h and Codex 7d on the LED strip
./mk-k68-lightsmart sessions

# send anything, and optionally read the reply
./mk-k68-lightsmart raw "04 18" --read
```

### Session limits on the strip

`sessions` cycles through Claude's 5-hour and 7-day windows, then Codex's
5-hour and 7-day windows, for about three seconds each. It uploads one
looping visor animation; the views switch without per-screen uploads. Key
lighting effects remain independent. Unused visor LEDs are black. The
percentages and bars show usage already consumed, not usage remaining. Values
are captured when the loop starts; run `sessions` again to refresh them.
`./mk-k68-lightsmart sessions --dry-run` prints the readings without
connecting to the keyboard.

Codex limits come from its local session records. Claude limits are read
from the same cache WatchAI uses: `~/.config/watchai/limits/claude.json`
(or `$XDG_CONFIG_HOME/watchai/limits/claude.json`). WatchAI's configured
Claude Code status line writes the official 5h and 7d percentages there;
`mk-k68-lightsmart` reads that credential-free snapshot directly and does
not access Claude credentials or call a network service. The values update
when Claude Code next runs its WatchAI status line. If the cache is missing
or a window has expired, that screen shows `--` until WatchAI writes a fresh
reading.

---

## Protocol, in brief

Full detail in [docs/PROTOCOL.md](docs/PROTOCOL.md). The shape of it:

Commands are **64-byte HID feature reports** on interface 2. The device
declares no report id, so the wire buffer is 65 bytes — a leading `0x00`
then the payload. On Windows the driver calls `HidD_SetFeature`; on
Linux that is `ioctl(fd, HIDIOCSFEATURE(65), buf)`.

Every transfer is paced **15 ms**. This is not politeness — the firmware
drops packets without it.

After each framed step the keyboard is read back and **byte 3** of the
reply is the status: `0x01` accepted, `0xFF` busy (resend once).

### Two payload shapes

```
04 <sub> ...                 control command
<mode> <R> <G> <B> 00×4 <p8> <p9> <p10> <p11> 00 00 AA 55     data frame
```

A data frame is 16 bytes ending in the magic `AA 55`; four of them fit
in one report.

### The envelope

Data frames never travel alone:

```
04 18                          begin
04 13 .. .. .. .. .. .. 01     select the lighting target
<data frame>                   (no ack for this one)
04 02                          apply
04 F0                          end
```

### Lighting one key

```
80 R G B 00 00 00 00 <light_index> 00 0F 0F 00 00 AA 55
```

Mode `0x80` sets a single key. One key per transaction — that is the
firmware's own limit, stated in the vendor manual too.

### Uploading to the strip

```
04 18                                      begin
04 31|33 .. .. .. .. .. .. <lo> <hi>       announce N 64-byte blocks
<header><frames…>AA 55                     bulk payload, 64 bytes at a time
04 02                                      apply
04 F0                                      end
```

`0x31` is the boot animation (134 frames max), `0x33` an ordinary one
(941 max). Both take the same 4-byte header: frame count then speed,
each little-endian 16-bit. Pixels are **three bytes each in R, G, B
order**, pre-scaled by brightness, 325 per frame = 975 bytes. LEDs are
addressed row-first: `index = row * 65 + col`.

Reading the animation header as 2 bytes shifts every pixel two bytes
early — the firmware takes the first pixel's two leading channels for
the speed, and the strip shows what looks like a green/blue swap. That
is how this was found; `docs/PROTOCOL.md` has the patterns that
separate the two explanations.

---

## How this was worked out

The vendor installer is Inno Setup; inside it, `DeviceDriver.exe` is a
1.9 MB MFC application that statically links hidapi.

1. `innoextract` to unpack the installer.
2. `radare2` + a Capstone script to find every call into the HID
   transport and recover the 64-byte buffer each caller builds, by
   walking backwards from the call and collecting the immediate stores.
   That produced the opcode table in one pass.
3. The vendor's own `KeyboardLayout.xml` gave the LED index per key.
4. The strip's geometry was measured off the manual's screenshots by
   counting cell pitch in the pixels — 24 px pitch, 65 across, 5 down.
5. The stock animations ship as a **SQLite** file, which settled the
   data model: `led_index` 0‥324, `rgb_value` as `0x00RRGGBB`.

Every protocol claim in `docs/PROTOCOL.md` cites the address it came
from, so it can be re-checked.

---

## Licence and provenance

GPL-3.0-or-later.

This is a clean interoperability reimplementation. It ships none of the
manufacturer's **code**, strings or binaries — the UI is drawn, the
palette was sampled to match rather than copied, and the vendor material
used while reverse-engineering stays out of the repository.

It does ship their **dot-matrix designs**. The eighteen animations in
`mk_k68_lightsmart/effects/` come from the library the manufacturer
publishes for this keyboard, converted from the GIFs they serve to the
65×5 the strip actually is (251 KB in total, from 29 MB of source).
They are included so the app is useful the moment it is installed, with
no download and no import. They are the manufacturer's artwork, not
this project's, and some of them depict characters belonging to other
parties again; `tools/convert_effects.py` regenerates them from a local
folder, so removing them is deleting a directory.

The five animations the program *generates* — Tetris, the runner and
the three rains — are this project's own.

The project is not affiliated with, endorsed by, or derived from the
source code of any keyboard manufacturer.
