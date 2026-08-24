# Hardware validation log — MT-VIKI HDMI matrix

**Status: EMPTY TEMPLATE. Nothing here has been run against a physical unit yet.**

This integration and [`PROTOCOL.md`](PROTOCOL.md) were built from the
[bitfocus/companion-module-mt-viki-matrix](https://github.com/bitfocus/companion-module-mt-viki-matrix)
specification, not from a device on a bench. This file is the checklist that
turns that spec into verified fact. Work through it top to bottom with a real
matrix, fill in the tables, and open a PR — every `spec-only-unverified` row in
`PROTOCOL.md` that this file confirms can be promoted.

Fill in the header, then work the sections in order. Later sections assume the
earlier ones passed.

---

## 0. Unit under test

| Field | Value |
| --- | --- |
| Model (label on chassis) | |
| Matrix size | |
| Firmware (`GetMCUFWVer` reply) | |
| `PING` reply string | |
| IP address | |
| **TCP port that answered** | |
| Date tested | |
| Tester | |
| `mtviki_probe.py` version | |

---

## 1. Safety rules — read before touching anything

1. **Do not open the device's Web GUI network page.** Some MT-VIKI firmware
   revisions have been reported to perform something indistinguishable from a
   factory reset when that page is saved — losing IP settings, scenes and EDID
   assignments. Everything in this checklist is doable over TCP. Stay out of the
   GUI.
2. **Do not run `SetEDIDData`.** Section 8 below is deliberately marked
   *optional / destructive*. Writing a malformed EDID can leave an input with no
   usable video until it is re-flashed. Do not attempt it unless you can restore
   the original bytes and you accept the risk.
3. **Record the starting state before you change anything.** Section 2 does this
   for you; keep the output.
4. Have physical access to the unit. Several steps require watching the front
   panel or listening for a beep.
5. Nothing in this checklist changes the device's IP address — no such command
   exists in the protocol, and the integration implements none.

---

## 2. Find the port and take a baseline

`mtviki_probe.py` is a stdlib-only, dependency-free tool at the repository root.
It does not import the integration.

### 2.1 Port scan

```bash
python3 mtviki_probe.py --host 192.168.1.200 scan
```

Tries **8080, 5000, 23, 4001** in order and reports which one answers `GetSW`
with an `SWS` line.

| Port | Connected? | Answered `GetSW`? | Raw reply |
| --- | --- | --- | --- |
| 8080 | | | |
| 5000 | | | |
| 23 | | | |
| 4001 | | | |

> If none answer, try `--timeout 5` and confirm the unit's IP from its front-panel
> LCD. Only one TCP client may be connected at a time on some firmware — close
> the vendor GUI first.

### 2.2 Read-only sweep

```bash
python3 mtviki_probe.py --host 192.168.1.200 --port <PORT> \
    probe --md PROTOCOL_VALIDATION.md
```

This runs every **read-only** command once and appends a results section to this
file. It sends nothing that changes device state.

Paste or let the tool append the generated table here:

<!-- probe --md output lands below this line -->

---

## 3. Core routing — `SW` / `GetSW` / `SWS`

Use the REPL for the interactive steps:

```bash
python3 mtviki_probe.py --host 192.168.1.200 --port <PORT> repl
```

| # | Send | Expected | Actual reply | Video actually switched? | Pass |
| --- | --- | --- | --- | --- | --- |
| 3.1 | `GetSW` | `SWS` + one value per **output** | | n/a | |
| 3.2 | — | Field count == number of outputs on this unit | | n/a | |
| 3.3 | `SW 2 1` | `SWS` with output 1 now reading `2` | | | |
| 3.4 | `SW 1 2 3` | `SWS` with outputs 2 and 3 now reading `1` | | | |
| 3.5 | `SW 3 1 2 3 4 5 6 7 8` (enumerate all outputs on this unit) | all values `3` | | | |
| 3.6 | `SW 99 1` (input out of range) | **no reply at all** | | | |
| 3.7 | `SW 1 99` (output out of range) | **no reply at all** | | | |

**Key question — is `SWS` really indexed by output?**
Route input 2 to output 1 *only*, then read `GetSW`.

| Observation | Result |
| --- | --- |
| Which token position changed? | |
| To what value? | |
| Confirms `SWS <in_for_out1> ... <in_for_outN>` | ☐ yes ☐ no |

If this is wrong, everything in the integration is wrong. Stop and report.

---

## 4. Unsolicited pushes (the `local_push` claim)

Leave the REPL connected and idle, then operate the unit physically.

| # | Action at the device | Line pushed? | Raw line | Latency (approx) |
| --- | --- | --- | --- | --- |
| 4.1 | Change a route from the **front panel** | | | |
| 4.2 | Change a route from the **IR remote** | | | |
| 4.3 | Toggle key lock from the **front panel** | | | |
| 4.4 | Toggle the beeper setting from the front panel (if reachable) | | | |
| 4.5 | Recall a scene from the front panel | | | |

| Question | Answer |
| --- | --- |
| Does the device push `SWS` unsolicited? | ☐ yes ☐ no |
| Does it push `KeyLockStatus` unsolicited? | ☐ yes ☐ no ☐ n/a |
| If no pushes at all, polling must be documented as **required** | |

---

## 5. Scenes

| # | Send | Expected | Actual | Pass |
| --- | --- | --- | --- | --- |
| 5.1 | Set a distinctive routing pattern, then `SceneSave 1` | `SceneSaveOK` | | |
| 5.2 | Change routing, then `SceneCall 1` | `SWS` restoring the saved pattern | | |
| 5.3 | `SceneSave 16` | `SceneSaveOK` | | |
| 5.4 | `SceneCall 16` | `SWS` | | |
| 5.5 | `SceneSave 17` | ? (probing the upper bound) | | |
| 5.6 | `SceneCall 17` | ? | | |
| 5.7 | `SceneSave 0` | ? (probing the lower bound) | | |
| 5.8 | `SceneCall <unsaved slot>` | ? (does it reply at all, and with what?) | | |

| Conclusion | Value |
| --- | --- |
| Actual usable scene range | |
| Do scenes survive a power cycle? | ☐ yes ☐ no |
| Integration's 1–16 validation is ☐ correct ☐ too narrow ☐ too wide | |

---

## 6. Key lock and the beep-gating experiment

### 6.1 Key lock

| # | Send | Expected | Actual | Front panel actually locked? | Pass |
| --- | --- | --- | --- | --- | --- |
| 6.1.1 | `GetKeyLock` | `KeyLockStatus 0` or `1` | | n/a | |
| 6.1.2 | `SetKeyLock 1` | `KeyLockStatus 1` | | | |
| 6.1.3 | `SetKeyLock 0` | `KeyLockStatus 0` | | | |
| 6.1.4 | `SetKeyLock 2` (invalid) | no reply | | n/a | |
| 6.1.5 | Power cycle, then `GetKeyLock` | does the setting persist? | | n/a | |

### 6.2 Beep enable

| # | Send | Expected | Actual | Front-panel keys click? | Pass |
| --- | --- | --- | --- | --- | --- |
| 6.2.1 | `GetBeepEn` | `BeepEn 0` or `1` | | n/a | |
| 6.2.2 | `SetBeepEn 1` then press a front-panel key | `BeepEn 1`, key clicks | | | |
| 6.2.3 | `SetBeepEn 0` then press a front-panel key | `BeepEn 0`, silent | | | |

### 6.3 THE BEEP-GATING EXPERIMENT

**This is the single most important open question in the protocol** (see
[`PROTOCOL.md` §6.1](PROTOCOL.md#61-is-beepononce-gated-by-beepen)). The whole
Locate button design hangs on it. Do it carefully and in this exact order; you
need to be within earshot of the unit.

| Step | Send | Listen for | Beep heard? | Any reply line? |
| --- | --- | --- | --- | --- |
| A | `SetBeepEn 1` | (ack `BeepEn 1`) | n/a | |
| B | `BeepONOnce` | a beep | ☐ yes ☐ no | |
| C | `BeepONOnce` ×3, ~0.35 s apart | three distinct beeps | ☐ yes ☐ no | |
| D | `SetBeepEn 0` | (ack `BeepEn 0`) | n/a | |
| E | `BeepONOnce` | a beep | ☐ yes ☐ no | |
| F | `BeepONOnce` ×3, ~0.35 s apart | three distinct beeps | ☐ yes ☐ no | |
| G | `SetBeepEn 1` again, `BeepONOnce` | a beep (confirms B was not a fluke) | ☐ yes ☐ no | |

**Verdict:**

| Outcome | Meaning | Action |
| --- | --- | --- |
| B beeps, E silent | `BeepONOnce` **is gated** by `BeepEn` | The current `async_locate()` workaround is **necessary**. Keep it. Update `PROTOCOL.md` §6.1. |
| B beeps, E beeps | `BeepONOnce` is **independent** of `BeepEn` | The workaround is unnecessary. Simplify `async_locate()` to just fire the pattern, drop the `SetBeepEn` wrapping, and update `tests/test_api.py::test_locate_wraps_beeps_when_beep_en_is_false`. |
| B silent, E silent | The command does nothing, or the unit has no beeper | Consider removing the Locate button, or note it as inert on this model. |
| Any reply line observed | The spec's empty response cell is wrong | Record the exact line; `api.py` currently sends `BeepONOnce` fire-and-forget with no wait. |

Also record:

| Question | Answer |
| --- | --- |
| Minimum interval at which beeps stay distinguishable | |
| Does a rapid burst overrun / merge into one long tone? | |
| Is the beep loud enough to locate the unit in a rack? | |

---

## 7. HDCP — the arbitration test

### 7.1 Read-only

| # | Send | Actual reply | Field count | Matches input/output count? |
| --- | --- | --- | --- | --- |
| 7.1.1 | `GetInPortHDCP` | | | ☐ inputs |
| 7.1.2 | `GetOutPortHDCP` | | | ☐ outputs |

| Question | Answer |
| --- | --- |
| Distinct values observed on the **input** list | |
| Does an input's value change when you plug/unplug an HDCP source? | ☐ yes ☐ no |
| Does it change between an HDCP 1.4 and an HDCP 2.2 source? | ☐ yes ☐ no |
| Best guess at input value semantics | |

### 7.2 Arbitrating the value contradiction

The vendor doc contradicts itself: `0=disable / 1=enable / 2=follow-input`
versus `0=off / 1=1.4 / 2=2.0 / 3=2.2`. The integration currently adopts the
**second** reading. The deciding question is simply **whether the device accepts
`3` at all**.

Set up an output feeding a display you can watch, with a known HDCP source.

| # | Send | Reply | Video on the sink | Notes |
| --- | --- | --- | --- | --- |
| 7.2.1 | `SetOutPortHDCP 1 0` | | | |
| 7.2.2 | `SetOutPortHDCP 1 1` | | | |
| 7.2.3 | `SetOutPortHDCP 1 2` | | | |
| 7.2.4 | **`SetOutPortHDCP 1 3`** | | | **the deciding test** |
| 7.2.5 | `SetOutPortHDCP 1 4` | (expect silence) | | upper-bound probe |
| 7.2.6 | `SetOutPortHDCP 99 1` | (expect silence) | | port-bound probe |

| Observation | Result |
| --- | --- |
| Is the reply the **full** positional list, or only the changed port? | ☐ full ☐ single |
| Does `3` produce a reply (i.e. is it accepted)? | ☐ yes ☐ no |
| With a **non-HDCP** sink attached, which mode passes video? | |
| With an **HDCP 2.2** source, which mode passes video? | |

**Verdict:**

| Outcome | Meaning | Action |
| --- | --- | --- |
| `3` accepted and echoed | HDCP-**version** reading is correct | Current integration mapping is right. Promote `PROTOCOL.md` §6.2 to resolved. |
| `3` silently ignored, only 0/1/2 work | disable / enable / **follow-input** reading is correct | Change `HDCP_MODES` in `const.py` to three entries and rewrite the four `select.output_hdcp.state` strings in `translations/en.json`. Wire values 0/1/2 are unchanged. |
| Behaviour matches neither | Record everything observed | |

---

## 8. EDID — *optional, partially destructive*

**Read [§1](#1-safety-rules--read-before-touching-anything) again before this
section.** 8.1 and 8.2 are safe. 8.3 is not.

### 8.1 `SetEDID` selection values (safe — reversible)

Walk `<sel>` upward on one input and record which values are accepted (a reply
means accepted; silence means rejected). Note the sink's reported resolution
after each.

| `SetEDID 1 <sel>` | Reply | Resolution / audio the source now sees |
| --- | --- | --- |
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |
| 8 | | |
| 9 | | |
| 10 | | |
| 11 | | |
| 12 | | |
| 13 | | |
| 14 | | |
| 15 | | |
| 16 | | |
| 17 | (expect silence) | |
| 0 | (expect silence) | |

| Conclusion | Value |
| --- | --- |
| Valid `<sel>` range | |
| Meaning of each preset (if discoverable from the vendor GUI/manual) | |
| Integration's 1–16 guess is ☐ correct ☐ too wide ☐ too narrow | |

### 8.2 `GetEDIDData` (safe — read only)

| # | Send | Reply keyword | Payload length (chars) | Payload looks like… |
| --- | --- | --- | --- | --- |
| 8.2.1 | `GetEDIDData 1` | | | ☐ hex ☐ other |
| 8.2.2 | `GetEDIDData 2` | | | |
| 8.2.3 | `GetEDIDData 99` | | | |

| Question | Answer |
| --- | --- |
| Is the payload exactly **512 characters** (256 bytes as hex)? | ☐ yes ☐ no — actual: |
| Does it start with the EDID header `00FFFFFFFFFFFF00`? | ☐ yes ☐ no |
| Case | ☐ upper ☐ lower ☐ mixed |
| Valid slot range | |

### 8.3 `SetEDIDData` — **DESTRUCTIVE, OPTIONAL, SKIP BY DEFAULT**

Only attempt this if you captured the original payload in 8.2 **and** you are
willing to lose that EDID slot.

| # | Send | Reply | Effect |
| --- | --- | --- | --- |
| 8.3.1 | `SetEDIDData 1 <the exact payload read back in 8.2.1>` | expect `SetEDIDData OK` | should be a no-op round trip |
| 8.3.2 | `GetEDIDData 1` — does it match what you wrote? | | |

| Conclusion | Value |
| --- | --- |
| Payload encoding confirmed | |
| Round trip is lossless | ☐ yes ☐ no |

---

## 9. Identity, network and LCD labels

| # | Send | Expected keyword | Actual reply | Pass |
| --- | --- | --- | --- | --- |
| 9.1 | `GetMCUFWVer` | `MCUVer` | | |
| 9.2 | `PING` | model literal | | |
| 9.3 | `GetIP` | `IP` | | |
| 9.4 | `GetIPMask` | `IPMask` | | |
| 9.5 | `GetTitleLable` | `TitleLable` | | |
| 9.6 | `SetTitleLable RACK1` | `TitleLable RACK1` | | |
| 9.7 | `GetServiceType` | `ServiceType` | | |
| 9.8 | `SetServiceType MEET` | `ServiceType MEET` | | |
| 9.9 | `GetServiceNum` | `ServiceNum` | | |
| 9.10 | `SetServiceNum 0042` | `ServiceNum 0042` | | |

| Question | Answer |
| --- | --- |
| Does the `PING` reply match `FHDM88LAMG`, or is it model-specific? | |
| Do the `Get*Lable` / `Get*Type` / `Get*Num` **getters** exist at all? (they are inferred, not documented) | ☐ yes ☐ no |
| Where do the label strings actually appear on the LCD? | |
| Max label length before truncation | |
| Are spaces allowed in a label? | ☐ yes ☐ no |
| Do labels survive a power cycle? | ☐ yes ☐ no |

---

## 10. Framing, timing and connection behaviour

| Question | Answer |
| --- | --- |
| Line terminator the device actually sends | ☐ `\r\n` ☐ `\n` ☐ `\r` |
| Typical command → echo latency | |
| Does a single reply ever arrive split across TCP segments? | ☐ yes ☐ no |
| Do multiple replies ever arrive in one segment? | ☐ yes ☐ no |
| How many simultaneous TCP clients does it accept? | |
| What happens to client A when client B connects? | |
| Does the device ever close an idle connection? After how long? | |
| Any banner or greeting on connect? | ☐ none ☐ … |
| Does it tolerate the three on-connect commands sent back to back with no delay? | ☐ yes ☐ no |
| Any command that produces an error/NAK line rather than silence? | |
| Behaviour on a very long / malformed line | |

---

## 11. Integration end-to-end

Once the protocol is confirmed, install the integration and check the UI.

| # | Check | Pass | Notes |
| --- | --- | --- | --- |
| 11.1 | Config flow connects and creates the entry | | |
| 11.2 | Correct number of Output selects appear | | |
| 11.3 | Changing an Output select switches real video | | |
| 11.4 | A front-panel change updates HA within a few seconds (push) | | |
| 11.5 | Key lock switch works both directions | | |
| 11.6 | Beep switch works both directions | | |
| 11.7 | Locate button beeps the unit | | |
| 11.8 | Scene buttons 1–8 recall correctly | | |
| 11.9 | Firmware / Model / IP / IP mask sensors populate | | |
| 11.10 | Output HDCP selects reflect and set correctly | | |
| 11.11 | Input EDID selects (enable them first) set correctly | | |
| 11.12 | Text entities write to the LCD | | |
| 11.13 | Unplug the matrix → entities go unavailable | | |
| 11.14 | Plug it back in → entities recover without an HA restart | | |
| 11.15 | Reload the integration cleanly | | |
| 11.16 | Diagnostics download contains sane `recent_traffic` | | |
| 11.17 | All six services work from Developer Tools → Actions | | |

---

## 12. Summary

| Section | Result |
| --- | --- |
| 2 — port and baseline | ☐ pass ☐ fail ☐ skipped |
| 3 — routing | ☐ pass ☐ fail ☐ skipped |
| 4 — unsolicited pushes | ☐ pass ☐ fail ☐ skipped |
| 5 — scenes | ☐ pass ☐ fail ☐ skipped |
| 6 — key lock / beep gating | ☐ pass ☐ fail ☐ skipped |
| 7 — HDCP arbitration | ☐ pass ☐ fail ☐ skipped |
| 8 — EDID | ☐ pass ☐ fail ☐ skipped |
| 9 — identity / labels | ☐ pass ☐ fail ☐ skipped |
| 10 — framing / timing | ☐ pass ☐ fail ☐ skipped |
| 11 — integration end to end | ☐ pass ☐ fail ☐ skipped |

**Changes required in `PROTOCOL.md` / the integration as a result:**

1.
2.
3.

**Raw traffic log** — attach the file produced by `--log traffic.txt`, or paste
the interesting excerpts:

```
```

---

## 13. Passive-discovery groundwork (MAC / OUI / DHCP / UDP broadcast)

The integration currently ships only an **opt-in** TCP network scan (see the
README's "Discovery" section) — it never listens for anything on its own,
and there is no mDNS/SSDP/DHCP-based autodiscovery. This section exists
purely to record the raw facts a future *passive* discovery mechanism would
need; filling it in does not imply one gets implemented immediately, and
none of it blocks anything above.

| # | Item | How to get it | Value |
| --- | --- | --- | --- |
| 13.1 | MAC address of the unit | Router/AP client list, or `arp -a <ip>` after pinging it | |
| 13.2 | MAC OUI (first 3 octets) | Looked up from 13.1 against an OUI database | |
| 13.3 | Does the OUI resolve to a recognisable vendor (MT-VIKI or an OEM/ODM)? | | ☐ yes ☐ no — vendor: |
| 13.4 | DHCP hostname the unit requests (if any) | Router's DHCP lease table / `dhcp-lease-list` | |
| 13.5 | DHCP vendor class identifier (option 60), if visible | Packet capture on the DHCP exchange, or router UI | |
| 13.6 | mDNS/SSDP traffic observed from the unit | `avahi-browse -a` / a packet capture for a minute after power-on | ☐ none seen ☐ something — details: |
| 13.7 | Does the unit answer a UDP broadcast on port **4000**? | Sibling MT-VIKI SKUs (a different product line) reportedly use UDP 4000 for a scan/config protocol; send a broadcast probe and note whether this unit replies at all, even with garbage | ☐ yes — reply: ☐ no ☐ untested |
| 13.8 | Any other UDP port that answers a broadcast (sanity-sweep 1990, 4001, and other common device-discovery ports) | | |

**Verdict / next steps:**

| Outcome | Meaning | Action |
| --- | --- | --- |
| OUI is a real, stable MT-VIKI (or consistent OEM) identifier | A MAC-OUI allow-list could gate a future zeroconf/DHCP-watcher discovery flow | Record the OUI(s) here and open a follow-up issue |
| Unit answers UDP 4000 (or any other broadcast) meaningfully | A UDP broadcast probe could become a real passive (or at least push-based) discovery mechanism, augmenting the current opt-in TCP subnet scan | Record the exact request/response bytes; a follow-up implementation would live in `api.py` as a second discovery primitive, gated the same way (opt-in, never automatic) |
| Nothing above yields anything usable | Passive discovery stays out of scope; the opt-in TCP scan remains the only discovery mechanism | Note that conclusion here so nobody re-investigates from scratch |
