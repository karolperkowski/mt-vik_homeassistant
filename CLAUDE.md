# CLAUDE.md

HA custom integration for MT-VIKI HDMI matrix switches (MT-HD0808 family,
2x2–16x16), built entirely from the reverse-engineered protocol of
bitfocus/companion-module-mt-viki-matrix. **Never validated on real hardware**
— that's the next phase; see LOGBOOK.md ("Next session") and
PROTOCOL_VALIDATION.md for the checklist. Update LOGBOOK.md whenever a
session lands work.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements_test.txt
pytest                                    # full suite (150 tests, ~60s)
pytest tests/test_api.py -q               # protocol client only
ruff check custom_components tests mtviki_probe.py && ruff format --check custom_components tests mtviki_probe.py
python3 mtviki_probe.py --host <ip> scan|probe|repl   # hardware probing CLI
```

Release: bump `version` in `custom_components/mtviki_matrix/manifest.json`,
move CHANGELOG `[Unreleased]` into a dated section, commit, `git tag vX.Y.Z`,
push tag → release.yml verifies (tag==manifest version, lint, tests) and
publishes. CI: tests.yml (ruff+pytest), validate.yml (HACS+hassfest).

## Hard rules

- **No Claude attribution on commits** — no Co-Authored-By/Claude-Session
  trailers, ever (history was scrubbed of them once already).
- `api.py` stays stdlib-only, zero HA imports (independently testable).
- No optimistic state updates — state changes only from device reply lines.
- ruff is pinned (0.16.4, ruff.toml, py312 target); lint+format must pass.
- Never expose factory-reset/IP-change as entities/services.

## Architecture

- `custom_components/mtviki_matrix/api.py` — asyncio TCP client + module-level
  `async_discover()`. Single `_parse_line` handles solicited AND pushed lines.
- `coordinator.py` — push-first DataUpdateCoordinator (optional backup poll);
  fires `mtviki_matrix_route_changed` per changed output; tracks last recalled
  scene for the current-scene sensor; holds input/scene naming helpers.
- `config_flow.py` — menus: user step (manual | subnet scan), options
  (polling | input names | scene names).
- Platforms: select (routing/HDCP/EDID), switch, button (scenes/locate),
  sensor, media_player, text. `www/mtviki-matrix-card.js` — self-served
  crosspoint card (vanilla JS, no imports; HTML-escape user strings).
- `tests/mock_matrix.py` — stateful protocol mock w/ fault injection (split
  frames, batched lines, pushes, drops, `unsupported=` command set).

## Protocol landmines (full spec: PROTOCOL.md)

- ASCII over TCP 8080; TX `\r\n`; split RX on `\n`, strip `\r`. 1-based ports.
- **No NAK** — silence is the only rejection signal.
- `SWS a b c…` is positional BY OUTPUT (value=input). Never hardcode field
  counts (16x16). Device pushes `SWS` unsolicited on front-panel changes.
- Misspellings are load-bearing: `SetTitleLable`; `GetEDIDData x` replies
  `EDIDData y …`; `BeepONOnce` casing exact, never replies.
- UNVERIFIED (spec-only, flagged in code/docs): HDCP mode values
  (contradictory vendor doc), EDID select range, SetEDIDData payload format,
  BeepONOnce-vs-BeepEn gating (locate() conservatively enables+restores).
