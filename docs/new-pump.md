# Asking for a new pump

This integration reads the Grundfos pumps it has a description for. Today that's the **ALPHA2
GO** and the **ALPHA HWR**. Other Grundfos pumps with Bluetooth talk the same protocol
(GENIbus), but each model keeps its values in its own places, and a guess could show wrong
values. So before a model is added, I need a recording of what a real one answers.

Someone may have asked for your model already: see the
[existing pump requests](https://github.com/83noit/ha-grundfos-ble/issues?q=is%3Aissue%20label%3A%22pump%20request%22).
If yours is there, add your recording to that issue instead of opening a new one.

Recording a pump takes a few minutes. There are two ways to do it.

## The easy way: from Home Assistant

Needs ha-grundfos-ble 0.3.0 or newer.

1. Go to Settings → Devices & services → *Add integration* → **Grundfos Bluetooth**.
2. Your pump appears in the list as *not supported yet (family X, type Y): add it to collect
   data*. Pick it.
3. Read the explanation and submit, then press the pump's Bluetooth button when Home Assistant
   asks. It pairs, reads everything it knows how to ask for, for up to a minute, and stops.
4. The pump is added with no sensors, and a notice under Settings → Repairs explains what's
   next.
5. On the Grundfos Bluetooth page, open the menu (⋮) on your pump's entry and choose
   **Download diagnostics**.
6. [Open a new-pump request](https://github.com/83noit/ha-grundfos-ble/issues/new?template=new_pump.yml)
   and attach that file.

The file holds the frames the pump sent and received while pairing, plus the family and type
numbers. It leaves out the pump's Bluetooth address, serial number and the name you gave it.
Have a look before you attach it anyway.

The pump never gets written to: the integration only reads.

When a release adds your pump, the notice under Settings → Repairs changes to *now supported*
and offers to set it up. Submit it and the sensors appear, using the pairing you already have,
without pressing the button again. If the pump no longer accepts that pairing, choose
*Reconfigure* on it and pair again.

## Without Home Assistant: the command line

If you'd rather not add the pump to Home Assistant, or your version is older, the
[grundfos-ble](https://github.com/83noit/grundfos-ble) library has a command line that makes
the same recording from a computer with Bluetooth near the pump:

```
python3 -m venv grundfos-venv
source grundfos-venv/bin/activate      # Windows: grundfos-venv\Scripts\activate
pip install "grundfos-ble>=0.5.0"

grundfos-ble scan
grundfos-ble pair <address>            # press the pump's Bluetooth button when asked
grundfos-ble info <address>
grundfos-ble probe <address>
grundfos-ble capture <address> my-pump.txt
```

If you have [uv](https://docs.astral.sh/uv/), `uvx --from "grundfos-ble>=0.5.0" grundfos-ble
<command>` does the same without a venv. (Version 0.4.0 works too, but there the flag is
`--include-serial` and the pump's name isn't left out.)

Attach `my-pump.txt` and paste what the other commands print. Before pasting, **replace your
pump's Bluetooth address everywhere** with something like `XX:XX:XX:XX:XX:XX`, including in the
command lines themselves, not just in the `scan` output. The capture file and the other output
don't contain the address. They leave out the serial number, production code and the pump's
name unless you add `--include-private`; don't add it for a public issue.

Pairing from a computer adds one more pairing (bond) on the pump, next to your phone and Home
Assistant.

## What else helps

- A screenshot of the **Grundfos GO app** taken around the same time, showing flow, head,
  power and the control mode. That's how I check the readings and units.
- The model as printed on the pump's label (for example *ALPHA2 GO 25-75* or *ALPHA 15-58F*),
  and the country you bought it in: some models differ by region.

The [new-pump request form](https://github.com/83noit/ha-grundfos-ble/issues/new?template=new_pump.yml)
asks for all of this.
