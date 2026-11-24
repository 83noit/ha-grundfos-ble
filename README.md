# Grundfos Bluetooth for Home Assistant

[![CI](https://github.com/83noit/ha-grundfos-ble/actions/workflows/ci.yml/badge.svg)](https://github.com/83noit/ha-grundfos-ble/actions/workflows/ci.yml)
[![HACS](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hacs.yml/badge.svg)](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hassfest.yml/badge.svg)](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hassfest.yml)
[![Release](https://img.shields.io/github/v/release/83noit/ha-grundfos-ble?sort=semver)](https://github.com/83noit/ha-grundfos-ble/releases)
[![HACS Default](https://img.shields.io/badge/HACS-Default-41BDF5.svg)](https://github.com/hacs/default)
[![License: MIT](https://img.shields.io/github/license/83noit/ha-grundfos-ble)](LICENSE)

Watch a **Grundfos ALPHA2 GO** circulator pump from Home Assistant, over Bluetooth. No cloud,
no account. It talks to the pump directly, through an ESPHome Bluetooth proxy, in the pump's
own language (GENIbus).

You get sensors for flow, head, power, speed, voltage, temperatures, control mode, setpoint,
alarms and warnings. It only reads: it never changes anything on the pump, and the
`grundfos-ble` version it uses refuses every write anyway.

It's built on the [grundfos-ble](https://github.com/83noit/grundfos-ble) library, which
[credits](https://github.com/83noit/grundfos-ble#acknowledgements) the projects it learned
from. Status: alpha, tested on one ALPHA2 GO XX-75.

This project has nothing to do with Grundfos.

## What you need

- A Grundfos ALPHA2 GO. Other Grundfos Bluetooth pumps, like the ALPHA3, ALPHA HWR and SCALA1,
  talk the same way but aren't supported yet. You can still add one from *Add integration*, for
  data collection: it pairs once, records what the pump answers and creates no sensors.
  Download its diagnostics and attach them to a request so support can be added: see
  [Asking for a new pump](docs/new-pump.md). Automatic discovery stays quiet about these pumps.
- Home Assistant 2026.11 or newer.
- **A Bluetooth proxy that can make connections, within a few metres of the pump.** An ESPHome
  device with `bluetooth_proxy:` works (it makes connections unless you turn that off). The
  connection has to come up within about three seconds of you pressing the pump's button, so
  a weak or distant proxy won't do. Shelly devices only pass on what they hear and can't
  connect, so they can't be used for this pump. **One connecting proxy that's clearly closest
  to the pump** works best: the pairing belongs to the proxy that made it, and Home Assistant
  might try a different one.
- An ESPHome on that proxy that's new enough to pair, and not one built with ESP-IDF 5.5.6
  (ESPHome 2026.10), which forgets a pairing after one connection. The integration opens a
  repair issue if it spots either.
- The pump switched on. Lots of setups switch the pump through a thermostat. That's fine: the
  sensors go unavailable while the pump is off and come back when it does. But you need it on
  to pair.

## Install

**Marketplace (Home Assistant 2026.11+):** Settings → Marketplace → search for "Grundfos
Bluetooth" → install. If it doesn't show up under *Add integration* afterwards, restart Home
Assistant.

**By hand:** copy `custom_components/grundfos_ble` into your `config/custom_components/` and
restart Home Assistant.

Home Assistant fetches the [`grundfos-ble`](https://pypi.org/project/grundfos-ble/) library
from PyPI by itself.

## Pair the pump

1. Home Assistant finds the pump on its own (Settings → Devices & services shows it under the
   name you gave it in Grundfos GO). Select *Add*. You can also add it by hand with *Add
   integration* → Grundfos Bluetooth.
2. The first screen lists the proxies that hear the pump, and which of them can connect to it.
   If none can, sort that out first.
3. Select *Submit*. A waiting screen comes up. **Now press the Bluetooth button on the pump.**
   The pump accepts pairing for about three seconds after the press. As soon as the integration
   sees the pump announce the press, it connects through the strongest proxy, pairs, reads the
   pump's identity and checks which optional values it has.
4. If you missed the window (the screen tells you), submit again and press the button as soon
   as the waiting screen is back. Pressing it before the screen appears doesn't work.

### The one thing to know about the bond

The pairing (the bond) is stored **on the proxy that paired**. That means:

- The pump can keep more than one bond. In testing, pairing the Grundfos GO app on a phone
  didn't disturb Home Assistant's bond, and the phone reconnected later without a button press.
  Nobody knows how many bonds it can keep.
- If you move, replace or re-flash the proxy, use *Reconfigure* on the integration to pair the
  new one. Home Assistant picks the proxy by signal strength and recent failures. A proxy
  without a bond gets turned away and Home Assistant moves on, so the odd failed reading is
  normal when several proxies hear the pump. Keeping one clearly closest avoids it.
- While the Grundfos GO app is connected, the pump stops advertising, so Home Assistant shows it
  as unavailable. It comes back about a minute after the app disconnects. Use *Disconnect* in
  the app rather than just closing it. While Home Assistant is connected (a few seconds per
  reading), the app has to wait. Polling pauses whenever the pump announces a button press, so
  it doesn't steal the pairing window from the app.

## Entities

| Entity | Notes |
|---|---|
| Flow, Head, Power, Speed | The main readings. Head has no device class (it's in metres of water). |
| Electronics temperature | Diagnostic. Which temperature sensor the pump reports here isn't confirmed. |
| Control mode, Setpoint | From what the pump is actually running. The setpoint's unit follows the mode: metres, rpm or m³/h. |
| Alarm code, Warning code | One of Grundfos's error codes, or `none` when all is well. Read over the connection. |
| Media temperature | The pump's own estimate of the water temperature. |
| Connected | Diagnostic: on while the pump is advertising and recent readings worked. |
| Voltage, Differential pressure, DC-link voltage, Current, DC power, Ambient temperature, PCB temperature, Running time in the last 24 h, Powered time since commissioning, Power-ups, Operation mode, flow limits, AutoNight, limiter flags, Starts in the last 24 h, Signal strength | Off by default; turn on the ones you want. DC power and the temperature slots are decoded from recorded traffic and marked unconfirmed. |
| Energy | Total energy, kept by the pump; it survives a power cut. |
| Start count, Operating time since commissioning, Current run time | Diagnostic counters. The first two count since the pump was first set up; the run time starts again at every power-up. |

## Options

*Configure* → poll interval, 15–600 s (60 s by default). Each reading opens a short connection,
and the integration only polls while the pump is advertising, so a pump switched off by its
thermostat costs nothing.

## How data is updated

The pump doesn't send updates by itself, so the integration asks it. Home Assistant's Bluetooth
integration listens for the pump's advertisements to know it's switched on and in range. It
hears one about every 8 s, though a computer next to the pump can catch several a second. While
the pump is there, and once the poll interval has passed, the integration connects, encrypts,
reads the values the pump has, and disconnects.

If a reading times out or loses the connection, it's tried once more on a fresh connection,
starting from the value that failed. If an optional value (alarms, statistics, energy and so
on) didn't come through in a reading, it shows as unavailable for that reading instead of
keeping an old value. A value that keeps getting no answer is skipped for more and more
readings (1, 2, 4 … 32). A reading usually takes a few seconds; with retries it's cut off at
75 s.

If no advertisement arrives for about 40 s, or three readings in a row fail, the entities go
unavailable, and readings from before a power cut aren't shown again. Each time that happens
you get one line in the log at info level, and another when the pump is back.

## Use cases

- Log when a thermostat-switched pump runs, and how long each run lasts.
- Check that the flow and head match what your heating circuit should be doing.
- Hear about a blocked rotor or the pump running dry without walking over to it.

## Examples

Get a notification for any active alarm (replace `my_pump` with your pump's name):

```yaml
alias: Pump alarm
triggers:
  - trigger: state
    entity_id: sensor.my_pump_alarm_code
    not_to:
      - none
      - unavailable
      - unknown
actions:
  - action: notify.notify
    data:
      message: "Pump alarm: {{ states('sensor.my_pump_alarm_code') }}"
```

## Troubleshooting

- **"No Bluetooth proxy that can make connections hears this pump."** Only listen-only
  scanners (Shelly, or a proxy with `active: false`) can see the pump. Add an ESPHome proxy, or
  move one closer.
- **"The pump refused pairing … took N s"** The connection arrived after the three-second
  window: the proxy is too far away or too slow. Move it closer, then try again.
- **Repair "Pair again".** The pump kept turning down the encrypted connection. That's
  expected if the proxy holding the bond was replaced or wiped. Until you pair again, the
  integration polls every 10 minutes, and the repair closes itself if a poll works. Only
  refusals through the proxy that holds the bond count. Nobody has seen the pump lose a bond by
  itself, and re-pairing hasn't been tried on real hardware yet, so please open an issue if you
  see this repair.
- **Repair "… forgets the pairing".** The proxy got turned away within half an hour of pairing,
  after a good reading. ESP-IDF 5.5.6 (ESPHome 2026.10) proxies forget a pairing after one
  connection. Build the proxy with a different version, or reboot it, then *Reconfigure*. The
  diagnostics list each proxy's model and ESPHome version.
- **Repair "… cannot pair".** The proxy's ESPHome can't pair; update it.
- **Repair "… has not answered for an hour".** The pump is advertising but every reading fails.
  Close the Grundfos GO app, restart the proxy, or *Reconfigure*.
- **Pairing says "the pump paired but did not answer".** Some ALPHA3 pumps encrypt and then
  never answer until they're reset to factory settings (esphome/issues#4736). Nobody has seen
  an ALPHA2 GO do it. Switch the pump off and on, then try again.
- **The values look odd.** Download the diagnostics (device page → ⋮ → Download diagnostics).
  They include the raw frames, the proxies that hear the pump with their signal strength, and
  which optional values the pump answered. Attach them to an issue.
- Turn on debug logging (integration page → ⋮ → Enable debug logging) to see each reading's
  proxy, timings and connection events. It covers `custom_components.grundfos_ble`,
  `grundfos_ble` and `ble_bond`. To see every frame sent and received, also set
  `ble_bond.frames` to debug (Developer tools → Actions → `logger.set_level`). This integration
  shortens Bluetooth addresses in its log lines to their last two bytes, and shows proxies by
  their shortened address, not their name. Home Assistant's own Bluetooth log lines
  (`habluetooth`, `bleak`) still show full addresses and proxy names, so check a log before you
  paste it into an issue.
- Before you open an issue, see [what to include](https://github.com/83noit/ha-grundfos-ble/blob/main/SUPPORT.md#reporting-a-problem).

## Known limitations

- Alpha, built in my spare time for my own setup: see
  [SUPPORT.md](https://github.com/83noit/ha-grundfos-ble/blob/main/SUPPORT.md) for what to expect.
- Read-only for now.
- Only ESPHome Bluetooth proxies have been tested. A local Linux (BlueZ) adapter can pair since
  0.1.2, but it hasn't been tested here, and a built-in adapter may drop polls, which show up
  as gaps in the history. An ESPHome proxy near the pump is the way to go. When you pair
  through a local adapter, it keeps the bond it already has unless the pump turns that bond
  down during the button window, so a *Reconfigure* you give up on leaves a working bond alone.
- One kind of pump (ALPHA2 GO). Any other one can only be added for data collection (see What
  you need).
- The bond lives on the proxy that paired (see above).
- The Grundfos GO app and Home Assistant can't both be connected at once.
- Some decoded values aren't confirmed yet, and they're labelled that way.

## Remove

Settings → Devices & services → Grundfos Bluetooth → ⋮ → *Delete*. It's fine to leave the bond
on the proxy. Then remove the integration in the Marketplace if you don't want the files any
more.

## Development

```sh
nix develop                      # or: uv sync && source .venv/bin/activate
ruff check . && ruff format --check .
mypy
pytest --cov
```

The tests use `pytest-homeassistant`, and replay the pump from frames recorded on a real
ALPHA2 GO (they ship with `grundfos-ble`).

To work against a local copy of the library, run `uv pip install -e ../grundfos-ble` in the
activated environment and call the tools directly: `uv sync` and `uv run` put the locked
release back.

## Licence

MIT, see [LICENSE](LICENSE).
