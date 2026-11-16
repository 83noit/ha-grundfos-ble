# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Needs grundfos-ble 0.2.0.

### Added

- Repair issues for a proxy that forgets its pairing right after making it (ESP-IDF 5.5.6,
  ESPHome 2026.10), for a proxy whose ESPHome cannot pair, and for an hour of failed readings
  while the pump is heard. Each closes after the next good reading. A forgetful proxy no longer
  sends you round the "pair again" loop every few minutes.
- Diagnostics list each proxy's model and firmware version, and what the last reading read.
- Adding an unsupported Grundfos pump by hand now reports its family and type numbers, as the
  README promised; automatic discovery cannot show them.
- Removing the entry offers the pump for discovery again.

### Changed

- **The "Shaft power" sensor is now "DC power"** (`dc_power`). The value is probably the DC
  power the Grundfos GO app shows. The entity keeps its ID and history.
- Entities go unavailable after three failed readings in a row instead of two.
- A refusal counts towards "pair again" only when the attempt went through the proxy that holds
  the pairing, as Home Assistant reports it per attempt, not whenever that proxy hears the pump.
- Re-pairing keeps the optional objects the pump had unless it now says it lacks them, and
  removes only those entities; a corrupted or silent answer no longer drops entities.
- Pairing records the proxy the pairing connection really used.
- A pump is judged present by adverts from proxies that can connect to it.
- Development: the tests run on Home Assistant 2026.11 with `pytest-homeassistant`, which
  replaces the deprecated `pytest-homeassistant-custom-component`. CI pins its actions to commit
  SHAs, audits the workflows with zizmor and gets weekly Dependabot updates after a week's
  cooldown.
- On a local BlueZ adapter, the host's bond is removed only when the pump refuses it inside the
  button window, and then pairing is retried once. An abandoned *Reconfigure* no longer strands
  the adapter.

### Fixed

- An optional value (alarms, warnings, operation, limiters, statistics, temperatures, energy)
  that a reading could not get kept showing the previous reading's value as current. It now
  shows as unavailable for that reading. A lost link during these reads is retried once on a
  fresh connection from the object that failed, and an object that stays silent is skipped
  for 1, 2, 4 … 32 readings instead of costing every later object its turn.
- Pairing failed, after the pump had already bonded, when one optional object did not answer
  during the capability probe; each retry added another bond. The probe now goes on without it.
- A timeout after a successful pairing was reported as a missed button window. It is retried
  once on a fresh connection and otherwise reported as "the pump paired but did not answer".
- A pairing flow could start while a reading still held the pump's link; it now waits for the
  reading to finish. A reading still running at unload is cancelled, so it can no longer start a
  re-pairing flow for an entry that is gone.
- Diagnostics could include the names of your Bluetooth proxies through Home Assistant's
  reachability text. They are replaced by redacted addresses.
- Every connection waits 0.5 s after subscribing, through proxies too; ESPHome measured lost
  first requests without it (grundfos-ble 0.2.0).
- The README said readings take under five seconds and named `grundfos-ble` 0.1.0.

## [0.1.4] - 2026-11-15

### Fixed

- On a local Bluetooth adapter, most polls connected and then got no answer
  ([#1](https://github.com/83noit/ha-grundfos-ble/issues/1)). Each connection there now waits
  for BlueZ to finish setting up the link and lets it settle before the first request, the two
  likeliest causes. A request left unanswered now logs whether nothing arrived or only pieces of
  a reply. Proxies are unchanged (grundfos-ble 0.1.3).
- A reply may take up to 10 s instead of 8 s, above the 8 s link supervision timeout
  (grundfos-ble 0.1.3).
- A poll may take up to 75 s instead of 60 s, so that its retry on a fresh link is not cut short
  now that replies may take up to 10 s (grundfos-ble 0.1.3).

## [0.1.3] - 2026-11-14

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

[0.1.4]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.4
[0.1.3]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.3
[0.1.2]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.2
[0.1.1]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.0
