# MT-VIKI HDMI Matrix — Home Assistant integration

[![hacs][hacs-badge]][hacs-url]

Local-push control of MT-VIKI HDMI matrix switchers (MT-HD0808 family and
siblings) over their plain-TCP ASCII protocol. No cloud, no polling by default,
no extra Python dependencies — the protocol client is vendored inside the
integration.

> ## ⚠️ Not yet validated on physical hardware
>
> This integration was written **entirely from a protocol specification**, namely
> the vendor documentation and implementation in
> [bitfocus/companion-module-mt-viki-matrix][ref-module]. **No MT-VIKI matrix has
> been connected to it.** Core routing is well evidenced (the reference module
> was tested on a real MT-HD0808), but a large part of the command table is
> spec-only and has never been exercised by anything.
>
> Commands marked *unverified* below may do nothing, may behave differently than
> documented, or may not exist on your unit. See [`PROTOCOL.md`](PROTOCOL.md) for
> the per-command evidence table, and [`PROTOCOL_VALIDATION.md`](PROTOCOL_VALIDATION.md)
> for the hardware checklist — **if you own one of these, filling that in is the
> single most useful contribution you can make.**

---

## What it does

* **Routing** — one `select` per output; pick which input feeds it. Also exposed
  as optional `media_player` entities for dashboards that prefer source pickers.
* **Local push** — the matrix announces route changes made from the front panel
  or the IR remote, so Home Assistant stays in sync without polling.
* **Scenes** — save and recall the device's own 16 routing presets.
* **Front-panel lock and beeper** — switches for both, plus a *Locate* button
  that beeps the unit so you can find it in a rack.
* **HDCP and EDID** — per-output HDCP mode selects, per-input EDID preset
  selects, per-input HDCP diagnostic sensors. *(All unverified.)*
* **LCD labels** — write the front-panel LCD text. *(Unverified.)*
* **Diagnostics** — downloadable state dump including the last 200 lines of raw
  TX/RX traffic.

Supported matrix sizes: **2x2, 4x2, 4x4, 8x8, 16x16**. Nothing in the parser
assumes a size; the field count is read from whatever the device sends.

---

## Installation

### HACS (recommended)

This repository is not in the HACS default store, so add it as a custom
repository:

1. HACS → **⋮** (top right) → **Custom repositories**
2. Repository: `https://github.com/karolperkowski/mt-vik_homeassistant`
   Type: **Integration**
3. **Add**, then find **MT-VIKI HDMI Matrix** in HACS and **Download**.
4. **Restart Home Assistant.**

### Manual

1. Download this repository.
2. Copy `custom_components/mtviki_matrix/` into your Home Assistant
   `config/custom_components/` directory, so you end up with
   `config/custom_components/mtviki_matrix/manifest.json`.
3. **Restart Home Assistant.**

---

## Setup

**Settings → Devices & Services → ＋ Add Integration → MT-VIKI HDMI Matrix**

| Field | Default | Notes |
| --- | --- | --- |
| **Host** | — | The matrix's IP address. Vendor factory default is `192.168.1.200`; the current one is shown on the front-panel LCD. |
| **Port** | `8080` | Documented port for the MT-HD0808. Sibling SKUs may use 23, 5000 or 4001 — see [Troubleshooting](#troubleshooting). |
| **Matrix size** | `8x8` | One of `2x2`, `4x2`, `4x4`, `8x8`, `16x16`. Determines how many entities are created. |

Setup makes a single connection attempt and asks the device for its routing
table; if that fails you get `cannot_connect` and nothing is created. The entry
is uniquely identified by `host:port`, so the same matrix cannot be added twice.

### Options

**Settings → Devices & Services → MT-VIKI HDMI Matrix → Configure**

| Option | Default | Notes |
| --- | --- | --- |
| **Enable polling** | off | The matrix pushes changes on its own, so polling is a backup for firmware that turns out not to. |
| **Poll interval** | 60 s | Minimum 10 s. Only used when polling is enabled. |

---

## Entities

All entities belong to a single device. `N_in` / `N_out` come from the
configured matrix size. **Disabled by default** entities exist immediately but
must be switched on in the entity registry (device page → the entity → settings
→ *Enabled*).

| Platform | Entity | Count | Category | Enabled by default | Verified? | What it does |
| --- | --- | --- | --- | --- | --- | --- |
| `select` | **Output *N*** | `N_out` | — | ✅ | ✅ verified | Choose which input feeds this output. Options `Input 1`…`Input N_in`. Sends `SW`. |
| `select` | **Output *N* HDCP** | `N_out` | config | ✅ | ⚠️ unverified | HDCP mode: *Off* / *HDCP 1.4* / *HDCP 2.0* / *HDCP 2.2*. An unrecognised raw value shows as unknown rather than guessing. Sends `SetOutPortHDCP`. |
| `select` | **Input *N* EDID** | `N_in` | config | ❌ **disabled** | ⚠️ unverified | EDID preset 1–16. The valid range is a guess — the vendor spec documents no values at all. Sends `SetEDID`. |
| `switch` | **Key lock** | 1 | config | ✅ | ✅ verified | Lock/unlock the front-panel keys. |
| `switch` | **Beep** | 1 | config | ✅ | ✅ verified | The front-panel **key-click** beeper (not the Locate beep). |
| `button` | **Locate** | 1 | — | ✅ | ✅ verified | `IDENTIFY` device class. Beeps the unit 4× so you can find it. See [the beep caveat](#the-locate-button-and-the-beep-question). |
| `button` | **Scene *N*** | 16 | — | ✅ for 1–8, ❌ for 9–16 | ✅ verified | Recall device scene *N*. |
| `sensor` | **Firmware version** | 1 | diagnostic | ✅ | ⚠️ unverified | `GetMCUFWVer`. |
| `sensor` | **Model ID** | 1 | diagnostic | ✅ | ⚠️ unverified | The `PING` reply string (a model literal, e.g. `FHDM88LAMG`). |
| `sensor` | **Device IP** | 1 | diagnostic | ✅ | ⚠️ unverified | `GetIP`. |
| `sensor` | **IP mask** | 1 | diagnostic | ✅ | ⚠️ unverified | `GetIPMask`. |
| `sensor` | **Input *N* HDCP** | `N_in` | diagnostic | ❌ **disabled** | ⚠️ unverified | Raw integer from `InPortHDCPS`. Deliberately uninterpreted — the vendor defines no semantics for the input list. |
| `media_player` | **Output *N*** | `N_out` | — | ❌ **disabled** | ✅ verified | Same routing as the Output select, as a `SELECT_SOURCE` media player. For dashboards/voice assistants that prefer that shape. |
| `text` | **Title label** | 1 | config | ❌ **disabled** | ⚠️ unverified | `SetTitleLable` (the vendor's misspelling is part of the protocol). |
| `text` | **Service type** | 1 | config | ❌ **disabled** | ⚠️ unverified | Front LCD line 1. |
| `text` | **Service number** | 1 | config | ❌ **disabled** | ⚠️ unverified | Front LCD line 2. |

There is deliberately **no** factory-reset action, **no** IP-change action, and
**no** raw EDID-data writer. See [`PROTOCOL.md` §6.4](PROTOCOL.md#64-setediddata-payload-format).

### The Locate button and the beep question

Whether the device's one-shot beep command (`BeepONOnce`) is silenced by the
key-click beeper setting is **undocumented and untested** — the vendor spec has a
blank response cell for it and the reference module never addresses it.

The integration assumes the pessimistic case: if it knows the beeper is off, it
turns it on, fires the beeps, then turns it back off (restoring your setting even
if the action is cancelled mid-pattern). If the beeper is already on, or its
state is unknown, it just beeps and changes nothing.

Cost if the assumption is wrong: two extra harmless commands.
[Section 6.3 of the hardware checklist](PROTOCOL_VALIDATION.md#63-the-beep-gating-experiment)
settles it in about two minutes with a real unit.

---

## Services

All six accept a target — either `device_id` or `config_entry_id`.

### `mtviki_matrix.set_route`

Route one input to one, several, or all outputs.

```yaml
# One output
action: mtviki_matrix.set_route
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  input: 3
  outputs: [1]

# Several outputs at once (a single SW command on the wire)
action: mtviki_matrix.set_route
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  input: 2
  outputs: [1, 3, 5]

# Every output
action: mtviki_matrix.set_route
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  input: 1
  outputs: all
```

### `mtviki_matrix.save_scene`

Store the current routing table in one of the device's own scene slots.

```yaml
action: mtviki_matrix.save_scene
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  scene: 4
```

### `mtviki_matrix.recall_scene`

```yaml
action: mtviki_matrix.recall_scene
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  scene: 4
```

`scene` is 1–16. That range is the reference module's convention rather than a
documented device limit.

### `mtviki_matrix.set_output_hdcp` ⚠️ unverified

```yaml
action: mtviki_matrix.set_output_hdcp
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  output: 2
  mode: 3   # 0 = off, 1 = HDCP 1.4, 2 = HDCP 2.0, 3 = HDCP 2.2
```

The vendor documentation contradicts itself about what these values mean; see
[`PROTOCOL.md` §6.2](PROTOCOL.md#62-hdcp-value-contradiction).

### `mtviki_matrix.set_input_edid` ⚠️ unverified

```yaml
action: mtviki_matrix.set_input_edid
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  input: 1
  edid: 4
```

Valid `edid` values are **not documented anywhere**. 1–16 is a guess.

### `mtviki_matrix.locate`

```yaml
action: mtviki_matrix.locate
data:
  device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
  count: 6        # 1–20, default 4
  interval: 0.25  # 0.1–2.0 s, default 0.35
```

### Example automation

```yaml
automation:
  - alias: "Movie night"
    triggers:
      - trigger: state
        entity_id: input_boolean.movie_night
        to: "on"
    actions:
      - action: mtviki_matrix.recall_scene
        data:
          device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
          scene: 2
      - action: mtviki_matrix.set_route
        data:
          device_id: 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d
          input: 1
          outputs: [1, 2]
```

---

## Lovelace example

A grid of the eight output selects plus a Locate button:

```yaml
type: grid
columns: 2
square: false
cards:
  - type: entities
    title: HDMI Matrix routing
    show_header_toggle: false
    entities:
      - entity: select.hdmi_matrix_output_1
        name: Living room TV
      - entity: select.hdmi_matrix_output_2
        name: Kitchen TV
      - entity: select.hdmi_matrix_output_3
        name: Bedroom TV
      - entity: select.hdmi_matrix_output_4
        name: Office monitor
      - entity: select.hdmi_matrix_output_5
        name: Projector
      - entity: select.hdmi_matrix_output_6
        name: Bar TV
      - entity: select.hdmi_matrix_output_7
        name: Patio TV
      - entity: select.hdmi_matrix_output_8
        name: Rack monitor
  - type: vertical-stack
    cards:
      - type: button
        entity: button.hdmi_matrix_locate
        name: Locate matrix
        icon: mdi:bullhorn
        show_state: false
      - type: entities
        title: Matrix control
        show_header_toggle: false
        entities:
          - entity: switch.hdmi_matrix_key_lock
          - entity: switch.hdmi_matrix_beep
          - entity: button.hdmi_matrix_scene_1
          - entity: button.hdmi_matrix_scene_2
          - entity: sensor.hdmi_matrix_firmware_version
```

> **Entity IDs.** These are generated from the device name, which defaults to the
> model string and IP (e.g. `FHDM88LAMG (192.168.1.200)` →
> `select.fhdm88lamg_192_168_1_200_output_1`). Rename the device to something
> short — *HDMI Matrix* — right after setup and the IDs above will match. Check
> **Developer tools → States** for the actual names.

---

## Troubleshooting

### It won't connect / `cannot_connect`

**The port is the usual culprit.** `8080` is what the MT-HD0808 vendor
documentation specifies, but MT-VIKI ships a lot of near-identical hardware under
different SKUs and white labels, and sibling units are commonly reported on
**23** (telnet), **5000**, or **4001** (serial-over-IP gateway). Even the
reference Companion module contains a stale `?? 23` fallback alongside its 8080
default.

The repository ships a standalone probe tool — pure stdlib, no dependencies, does
not import the integration:

```bash
# Which port speaks the protocol? Tries 8080, 5000, 23, 4001.
python3 mtviki_probe.py --host 192.168.1.200 scan

# Read-only sweep of every documented command, appended as a markdown table
python3 mtviki_probe.py --host 192.168.1.200 --port 8080 probe --md PROTOCOL_VALIDATION.md

# Interactive prompt — type raw commands, watch raw replies
python3 mtviki_probe.py --host 192.168.1.200 --port 8080 repl
```

Other things to check:

* Ping the IP first. The address on the front-panel LCD is authoritative.
* **Some firmware accepts only one TCP client at a time.** Close the vendor Web
  GUI and any Companion instance before testing.
* There is no authentication and no banner — if `telnet <ip> <port>` connects but
  `GetSW` returns nothing, it is the wrong port, not a login problem.
* The device gives **no error reply for a bad command**. Silence is the only
  failure signal the protocol has.

### ⚠️ Do not use the device Web GUI's network page

Several MT-VIKI units have been reported to behave like a **factory reset** when
the network settings page of the built-in web interface is saved — losing the IP
configuration, saved scenes and EDID assignments, and dropping back to
`192.168.1.200`.

Everything this integration needs is reachable over TCP. Nothing here writes the
device's network configuration, and no factory-reset action is exposed anywhere.
If you must change the device's IP, use the front panel.

### Entities are missing

Several entities are **disabled by default** (input EDID selects, input HDCP
sensors, media players, all three text entities, scene buttons 9–16). Enable them
on the device page. They are disabled because they are either unverified,
rarely useful, or would otherwise create dozens of entities on a 16x16.

### An unverified command does nothing

Expected, and not a bug you can fix from Home Assistant's side. About two thirds
of the command table has never been exercised on real hardware by anything —
neither by this project nor by the reference module it was derived from. The
device answers a command it does not implement with **silence**, which is
indistinguishable from a rejected command.

[`PROTOCOL.md`](PROTOCOL.md) marks every command
*verified-by-reference-module* or *spec-only-unverified*. If something in the
second group does not work, that is the expected outcome — please record it in
[`PROTOCOL_VALIDATION.md`](PROTOCOL_VALIDATION.md) and open an issue so the
docs can be corrected.

### State is stale after a front-panel change

The device is supposed to push `SWS` spontaneously when a route changes at the
unit. If yours does not, enable **polling** in the integration options. Please
also report it — whether pushes actually happen is one of the open questions.

### Getting a debug log

```yaml
# configuration.yaml
logger:
  default: warning
  logs:
    custom_components.mtviki_matrix: debug
```

Every TX and RX line is logged at debug level. The integration's **diagnostics
download** (device page → **⋮** → *Download diagnostics*) also contains the last
200 raw protocol lines with timestamps — attach that to bug reports.

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

The suite needs no hardware: `tests/mock_matrix.py` is an asyncio TCP server that
implements the full command table, including deliberate fault injection (replies
split across TCP writes, several replies batched into one write, unsolicited
pushes, and dropped connections).

`tests/test_mock_matrix.py` has **no Home Assistant dependency at all** and runs
with nothing but `pytest` + `pytest-asyncio`, so you can validate the mock even
if the HA test harness will not install on your Python version. See
[`requirements_test.txt`](requirements_test.txt) for version notes.

---

## Credits

The protocol implemented here comes entirely from
**[bitfocus/companion-module-mt-viki-matrix][ref-module]** — both its vendored
vendor documentation (`docs/spec.md`) and its implementation, maintained by
**Jens Frank**. Without that module's published spec this integration would not
exist. Thank you.

This project is not affiliated with, endorsed by, or supported by MT-VIKI or
Bitfocus. "MT-VIKI" is the manufacturer's trademark and is used here only to
identify the compatible hardware.

## License

See [`LICENSE`](LICENSE).

[ref-module]: https://github.com/bitfocus/companion-module-mt-viki-matrix
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[hacs-url]: https://github.com/hacs/integration
