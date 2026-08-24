# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Crosspoint Lovelace card (`mtviki-matrix-card`): a single-file, no-build
  custom card rendering the matrix as an inputs × outputs grid, with a row
  of scene buttons underneath. Ships inside the integration and is served
  and registered as a frontend resource automatically — no manual resource
  entry needed for UI-mode dashboards. Outputs can be listed explicitly or
  auto-discovered from the entity registry.

## [0.2.0] - 2026-08-24

### Added

- Opt-in network-scan discovery in the config flow (menu: manual entry or
  subnet scan; fingerprints devices by their `SWS` protocol reply).
- `mtviki_matrix_route_changed` event fired on the HA bus for every output
  routing change, including front-panel/IR changes.
- Release automation (tag-driven GitHub releases) and Dependabot updates.
- User-configurable input names and scene names, set via two new options-flow
  steps ("Name your inputs", "Name your scenes"). Names replace `Input N` in
  the output `select` entities' options, the `media_player` source lists, and
  the scene recall buttons' labels; renaming reloads the config entry so
  every entity picks up the new labels.
- **Current scene** sensor: reports the name of the last recalled scene while
  the routing still matches what that recall produced, `none` otherwise. The
  device exposes no way to read back scene contents or query which scene (if
  any) is active, so this is honestly "last recalled and unchanged since",
  not a real readback — see the README for the full caveat.

## [0.1.0] - 2026-08-23

### Added

- Initial release: local-push control of MT-VIKI HDMI matrix switchers
  (2x2, 4x2, 4x4, 8x8, 16x16) over their plain-TCP ASCII protocol.
- Routing `select` entities per output, plus optional `media_player` entities.
- Scene buttons to save/recall the device's 16 routing presets.
- Front-panel lock and beeper switches, and a *Locate* button.
- HDCP mode selects, EDID preset selects, and HDCP diagnostic sensors
  (unverified on real hardware).
- LCD label text entities (unverified on real hardware).
- Diagnostics download with the last 200 lines of raw TX/RX traffic.

[Unreleased]: https://github.com/karolperkowski/mt-vik_homeassistant/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/karolperkowski/mt-vik_homeassistant/releases/tag/v0.1.0
