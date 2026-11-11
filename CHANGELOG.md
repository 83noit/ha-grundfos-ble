# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- Pairing through a local Linux Bluetooth adapter failed with "Authentication Failed": Home
  Assistant registers no BlueZ pairing agent, so BlueZ refused the pump's request. grundfos-ble
  0.1.1 registers one while it pairs, and a refused pairing there now reads as a missed button
  window and raises a repair, as it does through a proxy
  ([#1](https://github.com/83noit/ha-grundfos-ble/issues/1)).
- Re-pairing through a local adapter could never succeed while the host kept an old bond: BlueZ
  reconnected with the old keys instead of pairing. Pairing now deletes the host's bond first.

## [0.1.1] - 2026-11-09

### Fixed

- Entities could stay unavailable after a long power-off although the pump was advertising
  again, when a scanner that cannot connect heard it before a proxy that can.
- Diagnostics no longer include the pump's name in the last error message.

## [0.1.0] - 2026-11-09

### Added

- First public release: reads a Grundfos ALPHA2 GO over Bluetooth LE through an ESPHome
  Bluetooth proxy. Read-only.

[0.1.1]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.0
