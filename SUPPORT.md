# Support

## What this project is

Grundfos Bluetooth is an alpha project. I built it for my own pump and my own Home Assistant,
and I'm sharing it early in case it helps someone else with the same pump. I work on it in my
spare time, so for now it follows my own needs first.

Feedback, bug reports and feature requests are very welcome: they show me how the integration
behaves outside my setup. But please don't count on any particular feature, timeline or
hardware. The surest way to get something in is a pull request, and I'm happy to review those.

## Features

The integration reads the pump; it doesn't control it. Changing settings, or starting and
stopping the pump, is off the table for now. Requests for that are still welcome as ideas for
later, but nobody's working on them in the meantime.

## Hardware

I develop and test on one Grundfos ALPHA2 GO, reached through ESPHome Bluetooth proxies (ESP32
boards running `bluetooth_proxy` with connections turned on). That setup is supported, and
problems with it get fixed. The ALPHA HWR is supported from one owner's recording and testing;
I don't have one.

Anything else is best effort, and here's why. Bluetooth problems nearly always depend on the
exact radio, its firmware and the Bluetooth stack on the host, so you only really get to the
bottom of them with that hardware in hand. I don't have a built-in or USB adapter running
through BlueZ, or any other Grundfos pumps, so I can't reproduce what you see. All I have is
what your logs and diagnostics show, and debugging through rounds of issue comments is slow
for both of us and mostly guesswork for me. I'll fix what the evidence points to, but I can't
promise more than that.

## Asking for a new pump

Follow [Asking for a new pump](docs/new-pump.md): it explains how to record your pump, from
Home Assistant or from a computer, and the request form asks for the rest.

## Reporting a problem

- Check the open issues first. If one matches your setup, add to it rather than opening a new
  one.
- Say which versions of the integration and Home Assistant you have, and whether you use a
  proxy or a local adapter.
- Attach a diagnostics download: device page → ⋮ → Download diagnostics.
- For connection problems, add a debug log. Turn on debug logging (integration page → ⋮ →
  Enable debug logging, which covers `custom_components.grundfos_ble`, `grundfos_ble` and
  `ble_bond`), wait for a few failed polls, then attach the log.
