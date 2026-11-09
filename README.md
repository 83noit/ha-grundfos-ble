# Grundfos Bluetooth for Home Assistant

[![CI](https://github.com/83noit/ha-grundfos-ble/actions/workflows/ci.yml/badge.svg)](https://github.com/83noit/ha-grundfos-ble/actions/workflows/ci.yml)
[![HACS](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hacs.yml/badge.svg)](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hacs.yml)
[![Hassfest](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hassfest.yml/badge.svg)](https://github.com/83noit/ha-grundfos-ble/actions/workflows/hassfest.yml)
[![Release](https://img.shields.io/github/v/release/83noit/ha-grundfos-ble?sort=semver)](https://github.com/83noit/ha-grundfos-ble/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/83noit/ha-grundfos-ble#install)
[![License: MIT](https://img.shields.io/github/license/83noit/ha-grundfos-ble)](LICENSE)

Reads a **Grundfos ALPHA2 GO** circulator pump over Bluetooth LE, through an ESPHome
Bluetooth proxy, using the pump's native GENIbus protocol. Flow, head, power, speed, voltage,
temperatures, control mode, setpoint, alarms and warnings become Home Assistant sensors. The
integration is read-only: `grundfos-ble` 0.1.0 does not write to pumps, and its write methods
raise `NotSupported`.

Built on [grundfos-ble](https://github.com/83noit/grundfos-ble), whose
[acknowledgements](https://github.com/83noit/grundfos-ble#acknowledgements) credit the projects
it draws on. Status: alpha, tested on one ALPHA2 GO XX-75.

This project is not affiliated with Grundfos.

## What you need

- A Grundfos ALPHA2 GO (other Grundfos Bluetooth pumps such as the ALPHA3, ALPHA HWR and
  SCALA1 use the same transport but are not yet supported; the integration tells you the
  family and type numbers it saw so support can be added).
- Home Assistant 2026.11 or newer.
- **A Bluetooth proxy that can make connections, within a few metres of the pump.** An
  ESPHome device with `bluetooth_proxy:` (active connections are the default) works; the
  connection has to come up within about three seconds of pressing the pump's button, which
  rules out weak or distant proxies. Shelly devices only relay advertisements and cannot be
  used for this pump.
- The pump powered. Many installations switch the pump through a thermostat; the integration
  copes (entities go unavailable while the pump is off and come back when it advertises), but
  pairing needs it on.

## Install

**Marketplace (Home Assistant 2026.11+):** Settings → Marketplace → *Add from link* →
`https://github.com/83noit/ha-grundfos-ble` → type *Integration* → install. Adding a repository
this way needs a GitHub connection, which Home Assistant asks for. If the integration does not
appear under *Add integration* afterwards, restart Home Assistant. Until this repository is in
the Marketplace catalog that is the way in; once listed, search for "Grundfos Bluetooth".

**Manually:** copy `custom_components/grundfos_ble` into your `config/custom_components/`
and restart Home Assistant.

Home Assistant installs the Python library
[`grundfos-ble`](https://pypi.org/project/grundfos-ble/) from PyPI automatically.

## Pair the pump

1. Home Assistant discovers the pump (Settings → Devices & services shows it under the name
   you gave it in Grundfos GO). Click *Add*. You can also add it by hand with
   *Add integration* → Grundfos Bluetooth.
2. The first screen lists which proxies hear the pump and which of them can connect. If none
   can, fix that first.
3. Submit. A waiting screen appears. **Now press the Bluetooth button on the pump.** The pump
   accepts pairing for about three seconds after the press; the integration connects through
   the strongest proxy the moment it sees the pump signal the press, pairs, reads the pump's
   identity and checks which optional values it offers.
4. If the window was missed (the screen says so), submit again and press the button as soon
   as the waiting screen is back. Pressing before the screen is shown does not work.

### The one thing to know about the bond

The pairing (bond) is stored **on the proxy that paired**. Consequences:

- The pump can hold more than one bond. Pairing the Grundfos GO app on a phone did not disturb
  Home Assistant's bond when tested, and the phone reconnected later without a button press.
  The maximum number of bonds is not known.
- If you move, replace or re-flash the proxy, use *Reconfigure* on the integration to pair the
  new one. Home Assistant picks the proxy by signal strength and recent failures. A proxy
  without a bond is refused and Home Assistant moves on, so an occasional failed reading is
  normal when several proxies hear the pump; keeping one clearly closest avoids it.
- While the Grundfos GO app is connected the pump stops advertising, so Home Assistant shows it
  as unavailable and recovers about a minute after the app disconnects. Use *Disconnect* in the
  app rather than only closing it. While Home Assistant is connected, for a few seconds per
  reading, the app has to wait. Polling pauses whenever the pump signals a button press, so as
  not to take the pairing window from the app.

## Entities

| Entity | Notes |
|---|---|
| Flow, Head, Power, Speed | Primary readings. Head has no device class (metres of water). |
| Electronics temperature | Diagnostic. Which temperature sensor the pump reports is not confirmed. |
| Control mode, Setpoint | From the pump's realised operation object. The setpoint's unit follows the mode: metres, rpm or m³/h. |
| Alarm code, Warning code | Enum of Grundfos error codes, `none` when clear. Read over the connection. |
| Media temperature | The pump's own estimate of the water temperature. |
| Connected | Diagnostic connectivity: on while the pump advertises and recent readings succeeded. |
| Voltage, Differential pressure, DC-link voltage, Current, Shaft power, Ambient temperature, PCB temperature, Running time in the last 24 h, Powered time since commissioning, Power-ups, Operation mode, flow limits, AutoNight, limiter flags, Starts in the last 24 h, Signal strength | Disabled by default; enable what you need. Shaft power and the temperature slots are decoded from captured traffic and marked unconfirmed. |
| Energy | Lifetime energy kept by the pump; it survives power loss. |
| Start count, Operating time since commissioning, Current run time | Diagnostic counters. The first two are lifetime values; the run time restarts at each power-up. |

## Options

*Configure* → poll interval, 15–600 s (default 60 s). Each reading opens a short connection
and polls only happen while the pump is advertising, so a pump switched off by its thermostat
costs nothing.

## How data is updated

The Bluetooth integration tracks the pump's advertisements (every ~8 s) to know it is powered
and in range. While it is, and once the interval has elapsed, the integration connects, encrypts,
reads the objects the pump supports, and disconnects. A timed-out reading is retried once on a
fresh connection, and what has already been read is kept.

If no advertisement arrives for about 40 s, or two readings in a row fail, entities become
unavailable; readings from before a power loss are not shown again. Each such episode logs one
line at info level, and one when the pump is back.

## Use cases

- Log when a thermostat-switched pump runs, and how long each run lasts.
- Check that flow and head match what the circuit should do.
- Be told about a blocked rotor or dry running without walking to the pump.

## Examples

Notify on any active alarm (replace `my_pump` with your pump's name):

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

- **"No Bluetooth proxy that can make connections hears this pump."** Only passive scanners
  (Shelly, or a proxy with `active: false`) see the pump. Add or move an ESPHome proxy.
- **"The pump refused pairing … took N s"** The connection arrived after the three-second
  window: the proxy is too far or too slow. Move it closer, then retry.
- **Repair "Pair again".** The pump repeatedly refused the encrypted link, which is expected
  when the proxy holding the bond was replaced or wiped. The integration polls every 10 minutes
  until you re-pair, and the repair closes itself if a poll succeeds. A bond lost by the pump
  itself has never been observed, and re-pairing has not yet been run on real hardware, so
  please open an issue if you see this repair.
- **Values look odd.** Download diagnostics (device page → ⋮ → Download diagnostics): it
  includes the raw frames, the proxies that hear the pump with signal strength, and which
  optional objects the pump answered. Attach it to an issue.
- Enable debug logging for `custom_components.grundfos_ble` and `grundfos_ble` for frame-level
  detail.

## Known limitations

- Read-only in this version.
- Only ESPHome Bluetooth proxies have been tested. A local adapter may connect, but BlueZ and
  WinRT pairing failures are not recognised, so a refused pairing there shows as a failed
  connection and raises no repair.
- One pump family (ALPHA2 GO). Others are refused with their identity numbers in the message.
- The bond lives on the proxy that paired (see above).
- The Grundfos GO app and Home Assistant cannot be connected at the same time.
- Several decoded fields are unconfirmed and labelled so.

## Remove

Settings → Devices & services → Grundfos Bluetooth → ⋮ → *Delete*. The bond on the proxy is
harmless to leave. Then remove the integration in the Marketplace if you no longer want the
files.

## Development

```sh
nix develop                      # or: uv sync && source .venv/bin/activate
ruff check . && ruff format --check .
mypy
pytest --cov
```

Tests use `pytest-homeassistant-custom-component`; the pump is replayed from frames recorded
on a real ALPHA2 GO (shipped with `grundfos-ble`). The config flow has 100 % coverage.

To develop against a local checkout of the library, run `uv pip install -e ../grundfos-ble` in
the activated environment and call the tools directly, because `uv sync` and `uv run` restore
the locked release.

## Licence

MIT, see [LICENSE](LICENSE).
