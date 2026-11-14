# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Diagnostics, and the log line when the pump becomes unreadable, include Home Assistant's own
  account of the Bluetooth paths to the pump: which adapters and proxies hear it, whether they
  can connect, their connection failures and free slots.

### Fixed

- The repair "Pair again" opened over and over on a local Bluetooth adapter whose link kept
  stalling or dropping, although the bond was fine. Polls that connect and then get no answer,
  or lose the link, now only count as failed polls. Only the pump refusing the encrypted link
  raises the repair. Since an open repair slows polling to every 10 minutes, this also shortens
  the gaps in the history ([#3](https://github.com/83noit/ha-grundfos-ble/issues/3)).
- A poll that retries on a fresh link now waits a moment first, so the Bluetooth stack can
  settle after the failed one.
- Setup closes a connection to the pump left open by an earlier run on a local adapter. The
  pump serves one client at a time, so such a link would fail every poll.
- A link that stalls for a moment is now waited out instead of dropped: each reply may take up
  to 8 s, above the link supervision timeout (grundfos-ble 0.1.2).
- Each connection attempt within a poll now goes through whichever adapter or proxy hears the
  pump best at that moment (grundfos-ble 0.1.2).

## [0.1.2] - 2026-11-11

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

[0.1.2]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.2
[0.1.1]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.0
