# Chevrolet Volt hacking

Notes from poking at a 2017 Chevrolet Volt (Gen 2) over its CAN buses, plus the tooling that came out of it.

Most of this exists because I wanted a charging limit, which the car does not offer, and the rest accumulated along the way.

:warning: This writes to the CAN bus of a 1.6 ton vehicle, read the warnings at the bottom before you send anything!

Everything here was verified on the car.
Where something is inferred rather than observed, it says so.

## What's in here

- [charge-limit.md](charge-limit.md): stopping the charge at a set percentage, and the 2 different state of charge scales the car reports
- [battery-model.md](battery-model.md): pack capacity, state of health, and where the usable energy figure comes from
- [heated-seats.md](heated-seats.md): reading and controlling the heated seats, and why the heated steering wheel cannot be touched
- [home-assistant/](home-assistant/): getting the car into Home Assistant over MQTT, with discovery configs and a dashboard
- [tools/](tools/): scripts for generating the Home Assistant entities and writing dashboards

## Sources

Very little here is original.
The useful part is the verification, and writing down where the existing sources are wrong or incomplete for a Gen 2.

### Voltage

`io.tripovan.voltage`, by thanxx, on [Google Play](https://play.google.com/store/apps/details?id=io.tripovan.voltage) and on the [App Store](https://apps.apple.com/us/app/voltage-volt-ampera-bolt-obd/id6774315064), where the listing carries a different seller name.

It already does charge control and much more on these cars.
The charge control sequence, the per model year pack constants, and most of the UDS PIDs came from decompiling the Android build.

Its command strings are obfuscated with a repeating key XOR over base64, so everything quoted in these documents is the decrypted form.

It works well. Its limitation is that it only gets to act while your phone is nearby and awake.

### opendbc

[commaai/opendbc](https://github.com/commaai/opendbc), specifically `gm_global_a_lowspeed_1818125.dbc`.
Signal layouts for every SW-CAN broadcast referenced here.

It records the 13 bit PID but not the priority or source bits that real frames carry, and a number of its signals are unused on a Gen 2 even though they are defined.

### OVMS

[openvehicles/Open-Vehicle-Monitoring-System-3](https://github.com/openvehicles/Open-Vehicle-Monitoring-System-3), the hardware and firmware this runs on.

The existing `vehicle_voltampera` component, originally by Marko Juhanne, already decoded a good part of the bus and provided the wakeup sequence, lock/unlock and preconditioning that the rest builds on.

### The car

Everything was checked against a 2017 Volt before being written down.
Where a source turned out to be wrong for this car, the documents say which and how.

Also used on the Home Assistant side: [Ultra Card](https://github.com/WJDDesigns/Ultra-Card) for the dashboard.

## The buses

The Volt has 2 that matter here.

- HS-GMLAN, 500 kbps, 11 bit IDs
    - Powertrain and diagnostics
    - This is where UDS requests to `0x7E4` and friends go
- SW-CAN (GMLAN low speed), 33.3 kbps, single wire, 29 bit IDs
    - Body, climate, charging, comfort features
    - Most of the interesting broadcasts live here
    - On OVMS this is `can4` and needs the SWCAN expansion board

SW-CAN 29 bit IDs are laid out as `priority(3) | PID(13) | source(13)`.
The DBC only records the PID, but real frames carry priority and source too, so PID `0x391` shows up on my car as `0x107220A9`.

Match on the PID rather than the whole ID if you want your code to work on someone else's car:

```c
((MsgID >> 13) & 0x1FFF) == 0x391
```

## What works

Everything below was sent to the car and did what it was supposed to.

- Charging stops at a configurable percentage, holds there, and resumes if the car restarts the charge on its own. It runs unattended.
- Doors lock and unlock.
- All 4 windows go up and down, with the car merely awake, no diagnostic session needed.
- The trunk releases.
- The internal combustion engine can be forced on and forced off while the car is powered, held by a keep-alive for as long as you leave it set.
- Remote climate starts and stops, for heating and for cooling.
- Both heated seats can be turned on, set to any of their 3 levels, and turned off.
- Horn, exterior light flash, and both at once (OnStar's "vehicle locate").
- The car can be woken from sleep, which most of the above needs first.
- Both states of charge, EV range, gasoline range and total range, drive cycle energy and distance, oil life, charge energy, TPMS, the 12V battery and a pile of other values are readable and land in Home Assistant.

## What does not

- Window positions cannot be read while the car is at rest. The frame carries a fixed idle pattern that is byte identical whether every window is shut or every window is down, so the position is not stale or encoded differently, it is simply not transmitted.
- The pedestrian tone never fired. Every attempt was rejected in every state tried, including the one the Voltage app uses, with frame bytes that matched the app's.
- Cabin temperature cannot be set. The setpoint does not show up on the bus in any form that can be replayed.
- The heated steering wheel is not on either bus. It appears to be a switch and a relay.
- Anything OnStar does in the cloud rather than on the car: remote diagnostics reports, recall and warranty lookups, dealer scheduling, stolen vehicle assistance, crash response.
- The "since last charge" counters cannot be reset on demand. The car clears them only when it reaches 100%, and it does that internally with nothing on the bus to copy.
- The charge history PIDs return 6 bytes where the only available decoder expects 4, and that decoder produces contradictory results here. See [charge-limit.md](charge-limit.md).

## Firmware

The car side of this is a modified OVMS `vehicle_voltampera` component.

It is not in this repo because it belongs in an OVMS fork, but the behavior is described in the documents above.

## Warnings

Keep every control path behind a config flag that defaults to off, and do not test any of it with the car anywhere it could move.

A few specific ones I learned the hard way, which appears to be the only way I learn them.

:warning: Unlocking from OVMS does not silence a triggered alarm, only the key fob does!

:warning: Pulling the 12 volt battery to stop an alarm restarts every module, and the charge port loses its pilot signal until you unplug and replug the cable!

:warning: Deferring a charge while the car is switched on works, but it does not look like it!

The car keeps drawing from the wall to hold the pack topped up without raising SOC, by an amount that varies with the climate settings, and the charging flags read as an active charge throughout.
Anything watching those flags will think the defer failed and keep retrying.
