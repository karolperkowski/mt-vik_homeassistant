# Project Logbook

Running log of progress, decisions, and open work for the MT-VIKI HDMI matrix
Home Assistant integration. Newest entries first. Update this file whenever a
work session lands something or a decision is made.

## Status at a glance

| Area | State |
|---|---|
| Integration (HACS-installable) | ✅ complete, unvalidated on hardware |
| Test suite | ✅ 150 passing (mock-based) |
| CI (lint, pytest, HACS, hassfest) | ✅ all green |
| Releases | v0.1.0 · v0.2.0 · v0.3.0 (tag-driven pipeline) |
| **Hardware validation** | ⬜ **blocked — no physical unit yet** |
| HACS default store + brands | ⬜ deferred until hardware-validated |
| Serial/RS-232 transport | ⬜ deferred until hardware available |
| Passive discovery (DHCP/OUI) | ⬜ needs MAC/OUI from real unit |

## Log

### 2026-08-24 — Cards, naming, releases
- Cut **v0.3.0**: crosspoint Lovelace card (`www/mtviki-matrix-card.js`),
  served and auto-registered by the integration; actions bumped to
  checkout@v7 / setup-python@v7.
- Cut **v0.2.0**: network-scan discovery in the config flow (SWS-reply
  fingerprint), `mtviki_matrix_route_changed` bus events, input/scene naming
  via options flow, current-scene sensor, release automation + Dependabot.
- Rewrote git history to strip all Claude attribution trailers (standing
  rule: none on future commits), force-pushed; repo made public; HACS +
  hassfest validation green.
- Note: an Anthropic API incident (529s) interrupted the build agents for
  ~30 min; work resumed on fallback capacity, no impact on output.

### 2026-08-23 — Initial build (no hardware)
- Repo started empty. Protocol sourced entirely from
  bitfocus/companion-module-mt-viki-matrix (MT-HD0808, ASCII over TCP 8080);
  key facts: `\r\n` commands, unsolicited `SWS` pushes (→ `local_push`),
  1-based ports, no NAK — silence is the only failure signal.
- Built: vendored asyncio client (`api.py`), config flow, push-first
  coordinator, all entity platforms, 6 services, diagnostics, probe CLI
  (`mtviki_probe.py`), 94-test suite with stateful mock matrix, HACS
  packaging, README/PROTOCOL docs, ruff config (71 findings fixed), CI.
- Released **v0.1.0**.

## Next session: hardware validation (when the unit arrives)

Work through `PROTOCOL_VALIDATION.md` end-to-end using `mtviki_probe.py`:

1. `python3 mtviki_probe.py --host <ip> scan` — confirm port 8080 speaks the
   protocol (fallbacks: 5000, 23, 4001; then RS-232 115200 8N1).
2. `probe --md PROTOCOL_VALIDATION.md` — read-only sweep, log all replies.
3. The four open questions (each section ends in a which-file-to-change verdict):
   - **BeepONOnce vs BeepEn gating** → affects `async_locate()` in `api.py`
   - **HDCP mode values** (0/1/2/3 = off/1.4/2.0/2.2 vs 0/1/2 = off/on/follow)
     → affects HDCP select in `select.py` + translations
   - **Valid SetEDID select values** (currently unvalidated 1–16)
   - **SetEDIDData payload format** (assumed 512 hex chars)
4. Record MAC/OUI + DHCP hostname + UDP:4000 broadcast behavior (§13) →
   enables passive discovery work.
5. Then: brands PR + HACS default-store submission; consider serial transport.
