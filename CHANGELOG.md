# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Opt-in network-scan discovery in the config flow (menu: manual entry or
  subnet scan; fingerprints devices by their `SWS` protocol reply).
- `mtviki_matrix_route_changed` event fired on the HA bus for every output
  routing change, including front-panel/IR changes.
- Release automation (tag-driven GitHub releases) and Dependabot updates.

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
