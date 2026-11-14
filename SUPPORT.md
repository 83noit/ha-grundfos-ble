# Support

## What this project is

Grundfos Bluetooth is an alpha project. I built it for my own pump and my own Home Assistant,
and I share it early because it might be useful to someone else with the same pump. I work on it
in my spare time, so the integration currently follows my use case first.

Feedback, bug reports and feature requests are very welcome: they show me how the integration
behaves outside my setup. But please don't expect any particular feature, timeline or hardware
support. The most reliable way to get something in is a pull request, and I'm happy to review
those.

## Features

The integration reads the pump; it does not control it. Writing settings or starting and
stopping the pump is off the table for now. Requests for writes stay welcome as input for later,
but they won't be worked on in the meantime.

## Hardware

What I develop and test on is one Grundfos ALPHA2 GO, reached through ESPHome Bluetooth proxies
(ESP32 boards running `bluetooth_proxy` with active connections). That setup is supported, and
problems with it get fixed.

Anything else is best effort, and here is why. Bluetooth problems almost always depend on the
exact radio, its firmware and the Bluetooth stack on the host, so they can only really be
understood with that hardware in hand. I don't have a built-in or USB adapter running through
BlueZ, or other Grundfos pumps, so I can't reproduce what you see. All I have is what your logs
and diagnostics show, and debugging through rounds of issue comments is slow for both of us and
mostly guesswork for me. I'll fix what the evidence points to, but I can't promise more.

## Reporting a problem

- Check the open issues first, and add to an existing one rather than opening a new one for the
  same setup.
- Include the integration and Home Assistant versions, and whether you use a proxy or a local
  adapter.
- Attach a diagnostics download: device page → ⋮ → Download diagnostics.
- For connection problems, add a debug log. Enable debug logging for
  `custom_components.grundfos_ble` and `grundfos_ble`, wait for a few failed polls, then attach
  the log.
