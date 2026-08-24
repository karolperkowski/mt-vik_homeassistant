# MT-VIKI HDMI matrix — TCP control protocol

Reference document for the `mtviki_matrix` Home Assistant integration.

**Provenance.** Everything below is derived from
[`bitfocus/companion-module-mt-viki-matrix`](https://github.com/bitfocus/companion-module-mt-viki-matrix)
— specifically its vendored vendor documentation `docs/spec.md` (header line:
`MT-HD0808 TCP Port 8080 commands`) and its implementation in `index.js` /
`actions.js`. The module is maintained by Jens Frank and is documented as
*"Tested on the MT-HD0808 8x8 matrix"*.

**None of this has been validated against physical hardware by this project.**
Every row carries a status column saying how much confidence it deserves. See
[`PROTOCOL_VALIDATION.md`](PROTOCOL_VALIDATION.md) for the hardware test plan.

---

## 1. Wire format

| Property | Value |
| --- | --- |
| Transport | Plain TCP, no TLS |
| Default port | **8080** (see [§5](#5-port-numbers) for the 23 / 5000 / 4001 caveat) |
| Default IP | `192.168.1.200` (vendor factory default) |
| Handshake / banner | None. The device sends nothing on connect; the client may transmit immediately |
| Authentication | None |
| Encoding | ASCII (UTF-8 safe) |
| TX terminator | `\r\n`, always appended |
| RX terminator | `\r\n` in practice; parse defensively |
| Argument separator | Single space; all indices **1-based** decimal, no zero padding |
| Error / NAK | **None exists.** A rejected or malformed command produces *no reply at all* |
| Keepalive | None at the application layer. `PING` exists but the reference module never sends it |

### Receiving

The device pushes unsolicited lines at any time (see [§4](#4-unsolicited-pushes)),
so the client is a continuous reader loop, not a request/response state machine.

The integration's reader (`api.py`):

1. Accumulates raw bytes in a buffer — a single reply **can** be split across two
   TCP segments, and several replies **can** arrive in one segment.
2. Splits the buffer on `\n`, keeping any trailing partial line for the next read.
3. `strip()`s each line (removing the `\r`) and skips empties.
4. Tokenises with `str.split()` — *not* `split(" ")`. The reference module uses a
   naive single-space split which produces empty tokens on double spaces; we
   deliberately do not copy that bug.
5. Dispatches on `tokens[0]`. Unknown keywords are logged at DEBUG and dropped.
   Malformed values are warned about and dropped, never fatal.

Both framing hazards are covered by tests
(`tests/test_api.py::test_split_frame_reassembly`,
`::test_batched_lines_in_one_write`), driven by the fault-injection knobs in
`tests/mock_matrix.py`.

### Sending

```
b"SW 3 1\r\n"
```

There is no queueing while disconnected: `api.py` raises `MTVikiConnectionError`
rather than silently dropping the command (the reference module drops it with a
debug log).

---

## 2. Command table

Status legend:

| Status | Meaning |
| --- | --- |
| **verified-by-reference-module** | The command is actually sent, and/or its reply actually parsed, by `companion-module-mt-viki-matrix`, which its author tested on real MT-HD0808 hardware. Second-hand evidence, but real. |
| **spec-only-unverified** | Present in the vendor `docs/spec.md` table, but never exercised anywhere in the reference module's code. No evidence any device has ever answered it. |

### 2.1 Routing

| TX | RX | Status | Notes |
| --- | --- | --- | --- |
| `SW <in> <out1> [<out2> ...]` | `SWS <in_for_out1> ... <in_for_outN>` | verified-by-reference-module | Route one input to one or many outputs. "All outputs" is just the enumerated form `SW <in> 1 2 3 ... N`. |
| `GetSW` | `SWS <in_for_out1> ... <in_for_outN>` | verified-by-reference-module | Sent on connect and in the (optional) poll timer. |

**`SWS` semantics — the single most important detail.** The reply is indexed
**by output**, and each value is the **input** feeding that output. Token *i*
after the `SWS` keyword is output *i*; its value is the input number. Both are
1-based.

The number of value tokens equals the number of **outputs** on the unit — 2 for a
2x2 or 4x2, 4 for a 4x4, 8 for an 8x8, 16 for a 16x16. The vendor spec's examples
show four values (`SWS 1 2 3 4`) because the doc was adapted from a 4x4-era
product; a code comment in the reference module shows eight. **Never hardcode a
field count.** `api.py` consumes whatever arrives; the test suite parametrises
every size in `MATRIX_SIZES`.

### 2.2 Scenes

| TX | RX | Status | Notes |
| --- | --- | --- | --- |
| `SceneSave <n>` | `SceneSaveOK` | verified-by-reference-module | Single-token ack, no space before "OK". |
| `SceneCall <n>` | `SWS ...` | verified-by-reference-module | Self-syncing: recall answers with the restored routing table. |

The range **1–16** is the reference module's convention (`CHOICES_SCENES` is a
hardcoded 1..16 loop), **not** a vendor-documented limit — `docs/spec.md` just
writes `SceneSave x`. `api.py` validates 1–16 and raises `MTVikiError` outside it.
Whether the device accepts more or fewer is unknown.

### 2.3 Front-panel lock and beeper

| TX | RX | Status | Notes |
| --- | --- | --- | --- |
| `SetKeyLock 1` / `SetKeyLock 0` | `KeyLockStatus <0\|1>` | verified-by-reference-module | Locks the front-panel keys. |
| `GetKeyLock` | `KeyLockStatus <0\|1>` | verified-by-reference-module | |
| `SetBeepEn 1` / `SetBeepEn 0` | `BeepEn <0\|1>` | verified-by-reference-module | Persistent enable for the **front-panel key-click** beep. |
| `GetBeepEn` | `BeepEn <0\|1>` | verified-by-reference-module | Reference polls this on connect only, never in the poll timer. |
| `BeepONOnce` | *(nothing)* | verified-by-reference-module (sent), reply **documented as empty** | Fire a single beep. Exact capitalisation is load-bearing: capital `O`, capital `N`, capital `O` in `Once`. |

### 2.4 Identity and network

| TX | RX | Status | Notes |
| --- | --- | --- | --- |
| `GetMCUFWVer` | `MCUVer 01.00.00` | spec-only-unverified | Dotted triplet. |
| `PING` | `FHDM88LAMG` | spec-only-unverified | Reply is a **model literal, not "PONG"**. `FHDM88LAMG` is clearly model-specific (`88` = 8x8), so a 16x16 unit almost certainly answers something else. Never match on the literal. |
| `GetIP` | `IP 192.168.1.186` | spec-only-unverified | |
| `GetIPMask` | `IPMask 255.255.255.0` | spec-only-unverified | |

There is **no** IP-set command in the spec, and this integration deliberately
implements none. See [§5](#5-port-numbers) for why writing device network
settings is out of scope.

### 2.5 HDCP

| TX | RX | Status | Notes |
| --- | --- | --- | --- |
| `GetInPortHDCP` | `InPortHDCPS v1 v2 ... vN` | spec-only-unverified | Read-only, one value per **input**, positional. Value semantics undefined by the vendor. |
| `GetOutPortHDCP` | `OutPortHDCPS v1 v2 ... vN` | spec-only-unverified | One value per **output**, positional. |
| `SetOutPortHDCP <out> <mode>` | `OutPortHDCPS v1 ... vN` | spec-only-unverified | Reply is the **full list of all outputs**, not just the changed one — same positional pattern as `SWS`. |

The meaning of `<mode>` is contradicted within the vendor spec itself. See
[§6.2](#62-hdcp-value-contradiction).

### 2.6 EDID

| TX | RX | Status | Notes |
| --- | --- | --- | --- |
| `SetEDID <in> <sel>` | `InPortEdid <in> <sel>` | spec-only-unverified | Per-input EDID preset. Reply keyword is singular `InPortEdid`, unlike the plural-S status keywords. Valid `<sel>` values are **not documented anywhere**. |
| `SetEDIDData <slot> <payload>` | `SetEDIDData OK` | spec-only-unverified | Payload encoding stated only as "ASCII format 256byte". |
| `GetEDIDData <slot>` | `EDIDData <slot> <data>` | spec-only-unverified | Note the asymmetry: the request is `GetEDIDData` but the reply keyword drops the `Get`, while `SetEDIDData`'s reply keeps the `Set`. Do not assume reply keyword == command keyword. |

### 2.7 Front-panel LCD labels

| TX | RX | Status | Notes |
| --- | --- | --- | --- |
| `SetTitleLable <s>` | `TitleLable <s>` | spec-only-unverified | **The misspelling "Lable" is load-bearing** and appears in both directions. Do not "fix" it. |
| `GetTitleLable` | `TitleLable <s>` | spec-only-unverified | The getter is inferred from the Set/Get symmetry elsewhere in the table; the spec lists only the setter. |
| `SetServiceType <s>` | `ServiceType <s>` | spec-only-unverified | Vendor comment: "LCD Readout1!" — front LCD line 1. |
| `GetServiceType` | `ServiceType <s>` | spec-only-unverified | Inferred getter, as above. |
| `SetServiceNum <s>` | `ServiceNum <s>` | spec-only-unverified | Vendor comment: "LCD Readout2!" — front LCD line 2. |
| `GetServiceNum` | `ServiceNum <s>` | spec-only-unverified | Inferred getter, as above. |

---

## 3. Connection lifecycle

### On connect

Three commands, back to back, no delay, in this exact order — copied from the
reference module:

```
GetSW
GetKeyLock
GetBeepEn
```

`api.py` then issues a **one-time** discovery block, tolerating a timeout on each
individually (they are all spec-only, so a real unit may simply ignore them):

```
GetMCUFWVer
PING
GetIP
GetIPMask
GetInPortHDCP
GetOutPortHDCP
```

The three live commands are re-sent after **every** reconnect; the discovery
block is not.

### Reconnect

`api.py` reconnects automatically with exponential backoff from 1 s to 30 s. The
reference module uses a flat 10 s interval; the exponential form is our choice,
so a device that is off overnight does not generate a connection attempt every
ten seconds.

### Polling

Off by default. The device pushes state changes, and the reference module's own
author describes polling as "just a backup mechanism". The integration exposes it
as an options-flow toggle (`enable_polling`, default off; `poll_interval`,
default 60 s, minimum 10 s). When enabled, a poll is `async_refresh()` — the same
three live commands.

---

## 4. Unsolicited pushes

The device sends `SWS ...` spontaneously when a route is changed from the front
panel or the IR remote. This is the reason the integration is `iot_class:
local_push`. `KeyLockStatus` is presumed to push the same way (the reference
module parses it identically and polls it only as a backup), but that is an
assumption, not an observation.

Every inbound line — solicited or not — goes through **one** parser that updates
`MatrixState`. There is no separate "response" path.

---

## 5. Port numbers

| Port | Evidence |
| --- | --- |
| **8080** | The vendor spec's own header line, and the reference module's config default. Treat as correct for MT-HD0808. |
| 23 | A stale telnet fallback at `index.js:66` (`this.config.port = this.config.port ?? 23`) that only fires when a stored config has no port at all. Contradictory dead code — but sibling MT-VIKI SKUs are widely reported to listen on 23. |
| 5000 | Commonly reported on other MT-VIKI / white-label matrix SKUs. |
| 4001 | Serial-over-IP gateway convention seen on some units. |

`mtviki_probe.py scan` tries 8080, 5000, 23 and 4001 in that order and reports
which one answers `GetSW` with an `SWS` line.

**Do not attempt to change the device's IP over this protocol.** No such command
exists in the spec, and the vendor Web GUI's network page has been reported to
behave like a factory reset on some firmware revisions. The integration
implements no network-write and no factory-reset action of any kind.

---

## 6. Open questions and how the integration handles them

### 6.1 Is `BeepONOnce` gated by `BeepEn`?

**Unknown.** `docs/spec.md` has an *empty response cell* for `BeepONOnce` and says
nothing about gating. The reference module's `beep` action fires it
unconditionally and its preset has no dependency on the `beepEnabled` feedback —
so the author either knew it was independent, or never tested the interaction.
There is no way to tell which from the source.

**How we handle it.** `api.py::async_locate()` assumes the *pessimistic* case
(that it **is** gated) and takes the safe path:

```
if state.beep_en is False:      # only when we positively know it is off
    SetBeepEn 1
    BeepONOnce  x count         # sleep(interval) between
    SetBeepEn 0                 # restore, shielded against cancellation
else:                           # True, or None (unknown) -> touch nothing
    BeepONOnce  x count
```

If the assumption is wrong, the only cost is two harmless extra `SetBeepEn`
round trips. The restore is `asyncio.shield`ed so a cancelled Locate cannot leave
the user's beeper switched on. The exact TX order is pinned by
`tests/test_api.py::test_locate_wraps_beeps_when_beep_en_is_false`.

Resolving this is the first experiment in `PROTOCOL_VALIDATION.md`.

### 6.2 HDCP value contradiction

The vendor spec gives two incompatible readings **on adjacent rows**:

| Row | Legend |
| --- | --- |
| `check output HDCP` | `0: Disable HDCP, 1: Enable, 2: Follow Inport Hdcp` |
| `set output HDCP` (continuation) | `0: off, 1: 1.4, 2: 2.0, 3: 2.2` |

The command syntax line itself says `SetOutPortHDCP Outport 0/1/2` — three
values — while the second legend introduces a fourth (`3`) that the syntax never
mentions. The second legend smells like a copy-paste from a different product's
documentation.

**How we handle it.** Per the build contract the integration **adopts the
HDCP-version reading** — `0=off, 1=HDCP 1.4, 2=HDCP 2.0, 3=HDCP 2.2` — because it
is the more granular of the two and is what the `SetOutPortHDCP` row itself
prints. Concretely:

* `api.py::async_set_output_hdcp()` accepts modes `0–3` and rejects anything else
  with `MTVikiError`.
* The per-output HDCP `select` entity offers exactly four options, with
  translation keys `off` / `hdcp_1_4` / `hdcp_2_0` / `hdcp_2_2`.
* A raw value the device reports that is outside `0–3` maps the entity state to
  `None` (unknown) rather than guessing.
* Input HDCP is surfaced as a **raw integer** diagnostic sensor with no
  interpretation at all, because the spec defines no semantics for the input
  list whatsoever.

If hardware testing shows the disable/enable/follow reading is correct, only the
four translation strings and the `HDCP_MODES` list need to change — the wire
values `0`, `1`, `2` are identical under both readings, and only `3` is at risk.
That experiment is in `PROTOCOL_VALIDATION.md`.

### 6.3 `SetEDID` selection values

**Entirely absent from the spec.** No enumeration, no range, no defaults, and the
reference module never sends the command.

**How we handle it.** The per-input EDID `select` entity offers `1`–`16`
(labelled "EDID 1" … "EDID 16"), is `entity_category: config`, and is
**disabled by default** so nobody trips over it accidentally. The service
`mtviki_matrix.set_input_edid` accepts 1–16 as well. Both are documented as
unverified guesses. Sending an out-of-range value is expected to produce silence
(no NAK exists), not damage.

### 6.4 `SetEDIDData` payload format

The spec says only *"x: which edid, xxxxxxxx is ASCII format 256byte"*.

A raw EDID is 256 bytes (two 128-byte blocks). "ASCII format 256byte" for binary
data almost certainly means **512 ASCII hex characters representing 256 bytes** —
but the spec never says "hex", and neither the byte order nor the case is stated.
The slot index range is also undocumented.

**How we handle it.** `api.py` passes `payload` through **verbatim** —
`SetEDIDData <slot> <payload>` — and performs no encoding, decoding or length
validation. `async_get_edid_data()` returns the raw remainder of the `EDIDData`
reply as a string. There is **no entity and no service** for EDID data; it is
reachable only via `async_send_raw()` / the diagnostics download. This is
deliberate: writing a malformed EDID to a matrix is one of the few things in this
protocol that could plausibly brick a port's video, and we will not expose a
one-click path to it from a guess.

### 6.5 Scene range

1–16 is the reference module's convention, not a documented device limit. We
validate to that range. If hardware accepts a wider range this only costs the
user access to scenes they probably do not have.

---

## 7. Reference-module bugs we deliberately did NOT port

Recorded so a future reader does not "restore" them by comparing against the
JavaScript.

| Reference behaviour | Our behaviour |
| --- | --- |
| `enable_beep` / `disable_beep` call `updateLock()` instead of `updateBeepEn()`, corrupting keylock state (`actions.js:132,142`) | State comes only from the device's own `BeepEn` / `KeyLockStatus` echo, so the class of bug cannot occur. |
| Optimistic local updates after every send, reconciled later by the device echo | **No optimistic updates at all.** `MatrixState` changes only when a device line is parsed. Pinned by `tests/test_api.py::test_no_optimistic_update`. |
| `outputRoute` hardcoded to 8 entries and seeded with an identity map, never resized for the configured matrix (`index.js` constructor) | `routes` starts empty (`{}`) and is populated purely from the first `SWS`. No size is ever assumed. |
| Frame extraction keys on `\n` only, while `\r`-only input would hang the parser | Buffer bytes, split on newline, `strip()`, skip empties. |
| Tokeniser is `split(' ')`, so double spaces yield empty tokens | `str.split()`. |
| `polling_interval` vs `poll_interval` key mismatch — a missing key yields a runaway ~1 ms timer (`index.js:65` vs `224`) | Single option key, schema-validated with a 10 s minimum. |
| State updates silently dropped whenever the socket is down, with an unguarded `this.socket` dereference | Parser is independent of connection state; `stop()` cancels cleanly. |
| No response timeout anywhere | Configurable per-command timeout; state-changing verified commands raise `MTVikiError` on total silence, spec-only commands tolerate it. |

---

## 8. Credits

Protocol knowledge in this document is derived entirely from
**[bitfocus/companion-module-mt-viki-matrix](https://github.com/bitfocus/companion-module-mt-viki-matrix)**
(module maintainer: Jens Frank), including its vendored vendor documentation
`docs/spec.md`. This project has no affiliation with MT-VIKI or with Bitfocus.
