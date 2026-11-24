# Changelog

Notable changes, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- A pump this integration can't read yet can be added from *Add integration* for data
  collection. It pairs once, records what the pump answers (for up to a minute), creates no
  sensors, and raises a notice explaining how to send the diagnostics file in an issue. When an
  update supports the pump, the notice offers to set it up with its sensors, using the pairing
  it already has.
- Diagnostics include the frames recorded while pairing, in grundfos-ble's fixture format,
  without the pump's address, serial number or name.

### Changed

- The README is rewritten in plainer English, along with this changelog and the support notes.
  On the way, the support notes now list `ble_bond` in the debug logging, and the README names
  `pytest-homeassistant`, which the tests have used since 0.2.0.
- Needs grundfos-ble 0.4.0, which adds a command line the integration doesn't use. Nothing
  changes for you.

## [0.2.1] - 2026-11-16

Everything in 0.2.1b1 below, checked on the real pump, plus:

### Changed

- "Enable debug logging" on the integration page now covers `ble_bond` too, which logs each
  connection's events. Every frame sent and received goes to `ble_bond.frames`, which stays off
  unless you set it to debug. Needs ble-bond 1.1.0, pinned so an existing install updates.

## [0.2.1b1] - 2026-11-16

Beta, to check the new link package on real hardware before 0.2.1.

### Changed

- Needs grundfos-ble 0.3.0, which now takes its Bluetooth link (pairing, noting which proxy
  each attempt used, the BlueZ pairing agent, one request at a time) from the new shared
  package [ble-bond](https://github.com/83noit/ble-bond). Nothing changes for you.

## [0.2.0] - 2026-11-16

Needs grundfos-ble 0.2.0. Everything in 0.2.0b1 and 0.2.0b2 below, tested on a real ALPHA2 GO,
plus:

### Changed

- Log lines from this integration shorten Bluetooth addresses to their last two bytes, and the
  "cannot read the pump" line shows each proxy by its shortened address instead of its name.
  The pump's name stays, since that's how you tell pumps apart.

### Fixed

- Diagnostics no longer call `device_registry.async_get_device`, which Home Assistant 2026.11
  deprecates and 2027.8 removes. A local adapter's model and firmware version are now found
  through its Bluetooth entry, the same way as a proxy's.

## [0.2.0b2] - 2026-11-16

Beta, with fixes from testing 0.2.0b1 on the real pump.

### Fixed

- Diagnostics now find each ESPHome proxy's model and firmware version. They looked the proxy
  up by its Bluetooth MAC address, but ESPHome registers the device under its Wi-Fi MAC
  address. Now they follow Home Assistant's own link from the proxy's Bluetooth entry to its
  device.
- Diagnostics keep what the last reading read, how long it took and which proxy it went
  through after the pump stops advertising, instead of showing an empty list.
- No reading starts when the pump's last advertisement is more than 20 s old. A pump switched
  off by a thermostat or timer still got polled once, about 25 s after the power went, which
  only added a failure against the proxy.

### Added

- Debug logging shows each reading's proxy, how long connecting and the whole reading took,
  and what it read.

## [0.2.0b1] - 2026-11-16

Beta, for testing on real hardware before 0.2.0. Needs grundfos-ble 0.2.0.

### Added

- Repair issues for a proxy that forgets its pairing right after making it (ESP-IDF 5.5.6,
  ESPHome 2026.10), for a proxy whose ESPHome can't pair, and for an hour of failed readings
  while the pump can be heard. Each one closes after the next good reading. A forgetful proxy
  no longer sends you round the "pair again" loop every few minutes.
- Diagnostics list each proxy's model and firmware version, and what the last reading read.
- Adding an unsupported Grundfos pump by hand now tells you its family and type numbers, as
  the README promised. Automatic discovery can't show them.
- Removing the entry lets Home Assistant discover the pump again.

### Changed

- **The "Shaft power" sensor is now "DC power"** (`dc_power`). The value is probably the DC
  power the Grundfos GO app shows. The entity keeps its ID and history.
- Entities go unavailable after three failed readings in a row instead of two.
- A refusal only counts towards "pair again" when the attempt went through the proxy that holds
  the pairing, as Home Assistant reports it for each attempt, not just whenever that proxy can
  hear the pump.
- Re-pairing keeps the optional values the pump had unless it now says it doesn't have them,
  and only removes those entities. A garbled or missing answer no longer drops entities.
- Pairing notes the proxy the pairing connection really went through.
- Whether the pump is there is now judged only by advertisements from proxies that can connect
  to it.
- Development: the tests run on Home Assistant 2026.11 with `pytest-homeassistant`, which
  replaces the deprecated `pytest-homeassistant-custom-component`. CI pins its actions to
  commit SHAs, checks the workflows with zizmor, and gets weekly Dependabot updates once a
  release is a week old.
- On a local BlueZ adapter, the host's bond is only removed when the pump turns it down inside
  the button window, and then pairing is tried once more. Giving up on *Reconfigure* halfway no
  longer leaves the adapter without a bond.

### Fixed

- An optional value (alarms, warnings, operation, limiters, statistics, temperatures, energy)
  that a reading couldn't get kept showing the previous reading's value as if it were current.
  It now shows as unavailable for that reading. If the connection drops during these reads,
  it's tried once more on a fresh connection, starting from the value that failed. A value that
  keeps getting no answer is skipped for 1, 2, 4 … 32 readings, instead of using up the time
  every later value needs.
- Pairing failed, after the pump had already bonded, when one optional value didn't answer
  while the integration was checking what the pump has; every retry added another bond. That
  check now carries on without it.
- A timeout after a successful pairing was reported as a missed button window. Now it's tried
  once more on a fresh connection, and otherwise reported as "the pump paired but did not
  answer".
- A pairing flow could start while a reading still had the pump's connection; now it waits for
  the reading to finish. A reading still running when the integration unloads is cancelled, so
  it can't start re-pairing for an entry that's gone.
- Diagnostics could include the names of your Bluetooth proxies, through Home Assistant's text
  about how the pump can be reached. They're now swapped for redacted addresses.
- Every connection waits 0.5 s after subscribing, through proxies too. ESPHome found first
  requests getting lost without it (grundfos-ble 0.2.0).
- The README said readings take under five seconds, and named `grundfos-ble` 0.1.0.

## [0.1.4] - 2026-11-15

### Fixed

- On a local Bluetooth adapter, most polls connected and then got no answer
  ([#1](https://github.com/83noit/ha-grundfos-ble/issues/1)). Each connection there now waits
  for BlueZ to finish setting up the link, and lets it settle before the first request: the two
  likeliest causes. When a request goes unanswered, the log now says whether nothing came back
  or only pieces of a reply. Nothing changes on proxies (grundfos-ble 0.1.3).
- A reply can take up to 10 s instead of 8 s, longer than the 8 s link supervision timeout
  (how long the link waits before it gives up on a quiet partner) (grundfos-ble 0.1.3).
- A poll can take up to 75 s instead of 60 s, so its retry on a fresh connection isn't cut
  short now that replies can take up to 10 s (grundfos-ble 0.1.3).

## [0.1.3] - 2026-11-14

### Added

- The diagnostics, and the log line when the pump can't be read, include Home Assistant's own
  view of the Bluetooth paths to the pump: which adapters and proxies hear it, whether they can
  connect, their connection failures and free slots.

### Fixed

- The "Pair again" repair kept opening on a local Bluetooth adapter whose link kept stalling or
  dropping, even though the bond was fine. Polls that connect and then get no answer, or lose
  the connection, now only count as failed polls. Only the pump turning down the encrypted
  connection raises the repair. An open repair slows polling to every 10 minutes, so this also
  shortens the gaps in the history ([#3](https://github.com/83noit/ha-grundfos-ble/issues/3)).
- A poll that retries on a fresh connection now waits a moment first, so the Bluetooth stack
  can settle after the failed one.
- Setup closes a connection to the pump that an earlier run left open on a local adapter. The
  pump only serves one client at a time, so a link like that would make every poll fail.
- A link that stalls for a moment is now waited out instead of dropped: each reply can take up
  to 8 s, longer than the link supervision timeout (grundfos-ble 0.1.2).
- Each connection attempt within a poll now goes through whichever adapter or proxy hears the
  pump best right then (grundfos-ble 0.1.2).

## [0.1.2] - 2026-11-11

### Fixed

- Pairing through a local Linux Bluetooth adapter failed with "Authentication Failed". Home
  Assistant doesn't register a BlueZ pairing agent, so BlueZ turned down the pump's request.
  grundfos-ble 0.1.1 registers one while it pairs, and a refused pairing there now counts as a
  missed button window and raises a repair, just like through a proxy
  ([#1](https://github.com/83noit/ha-grundfos-ble/issues/1)).
- Re-pairing through a local adapter could never work while the host kept an old bond: BlueZ
  reconnected with the old keys instead of pairing. Pairing now deletes the host's bond first.

## [0.1.1] - 2026-11-09

### Fixed

- Entities could stay unavailable after a long power cut, even though the pump was advertising
  again, if a scanner that can't connect heard it before a proxy that can.
- Diagnostics no longer include the pump's name in the last error message.

## [0.1.0] - 2026-11-09

### Added

- First public release: reads a Grundfos ALPHA2 GO over Bluetooth LE through an ESPHome
  Bluetooth proxy. Read-only.

[Unreleased]: https://github.com/83noit/ha-grundfos-ble/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.2.1
[0.2.1b1]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.2.1b1
[0.2.0]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.2.0
[0.2.0b2]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.2.0b2
[0.2.0b1]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.2.0b1
[0.1.4]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.4
[0.1.3]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.3
[0.1.2]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.2
[0.1.1]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.1
[0.1.0]: https://github.com/83noit/ha-grundfos-ble/releases/tag/v0.1.0
