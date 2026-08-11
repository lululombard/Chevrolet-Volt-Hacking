# How this was done

A dated log of the 4 weeks it took to get a 2017 Chevrolet Volt (Gen 2) talking to OVMS, written down mostly for the dead ends.

I started at "does OVMS even support this car" and ended with a module that holds the charge at a set percentage, moves the windows, runs the engine on demand, and feeds 100+ values into Home Assistant.

The things that worked take a paragraph each.
The things that did not took days, and none of them are recorded anywhere else.

## July 23 to 30: before the hardware arrived

The whole project started from one missing feature, aside from the desire to own my own car telemetry data of course.
The Volt will not stop charging at a set percentage.
It charges to 100% and that is the only option the car offers.

The Voltage Android/iOS app can do it, but only while the phone is nearby, awake, and connected to an OBD dongle, which makes it pretty useless overnight.

OVMS already had a `vehicle_voltampera` component, so the first question was whether it covered a Gen 2.
It did not really.
The component was written against a Gen 1 and most of what it decoded either moved or changed scale between the 2 generations.

## July 31: first contact

The module arrived.

The first surprise was that the SWCAN driver is not in the stock firmware shipped with the OVMS kit.
Body, climate, charging and comfort all live on the low speed single wire bus, and none of it is reachable without a different build and a partition change.
The edge builds from dexters-web have it.

The second surprise was that the bus is asleep almost all the time.
The first command that produced any response at all was a wakeup, which flips the SWCAN transceiver into high voltage wakeup mode.
Nearly everything later in this document turns out to depend on that.

## August 1 to 2: capture tooling, and the first thing that never worked

I built a logging script against the module's HTTP command endpoint so frames could be recorded around a specific action.

First target was cabin temperature.
The car takes a temperature setpoint from the HMI, and remote climate has no way to set one, so it seemed like the obvious first win.
It was not.

Recording the bus while walking the setpoint from 24 to 20 to 26 and back produced nothing that could be replayed.
Spoofing a plausible frame changed nothing and the HMI did not react at all.
Dropped, and never revisited.

Pulling the cabin heater to maximum turned out to be the most useful test load available: 8 to 10 kW visible on the dash, on demand, with the car parked.
I checked most of the power and current decoding against it.

### The DBC catch

The signal layouts came from opendbc, specifically `gm_global_a_lowspeed_1818125.dbc`.
It describes the low speed broadcasts well, with one catch that cost me time: it records the 13 bit PID but not the priority and source bits that real frames carry.

A frame the DBC calls `0x391` arrives as `0x107220A9`.

:warning: Anything matching on the whole arbitration ID works on exactly one car!

```c
((MsgID >> 13) & 0x1FFF) == 0x391
```

With that understood, everything the DBC described went in at once: locks, lights, and the rest of the body broadcasts.

## August 3: the app

I decompiled the Voltage app.
Its command strings are obfuscated with a repeating key XOR over base64, so the first pass produced nothing readable and the second pass produced everything.

Out of it came the charge control sequence, the per model year pack constants, and most of the UDS PIDs.
Two details mattered more than the rest:

- The charge defer is a loop, not a one shot. The car resumes charging on its own and the defer has to be re-applied every time.
- A bare mode and time write is acknowledged by the HPCM and then ignored. The session keeps charging and the dash stays on Immediate.

Then I ran through everything the app claimed it could do, against the actual car:

- Preheat already worked in OVMS. Unlock needed a PIN of 0000, not blank.
- Windows worked first attempt, all 4, with the car awake. One frame to node `0x241`, no diagnostic session, no climate running.
- Trunk worked, on the same multiplexed OnStar frame as the locks.
- Horn and lights worked, individually and together, which is what OnStar calls vehicle locate.
- The engine needed a 3 state model, AUTO, FORCE_ON and FORCE_OFF, with a keepalive ticker, because the diagnostic session on `0x7E1` times out in about 5 seconds and the engine stops with it.

## August 4 to 11: the charge limit, properly

The sequence that actually works spoofs the car clock:

1. Write the car clock to 12:00.
2. Clear the departure time, force the mode to Immediate so the HPCM replans, then set the mode to departure based and write a 23:45 departure.
3. Restore the real clock.

The spoof only runs when there is a real clock available to restore afterwards.

:warning: Leaving a car convinced it is permanently noon is a worse outcome than a charge that does not defer!

Then it has to be re-applied, because the car keeps resuming, which turns the whole thing into a loop with an attempt cap so it gives up rather than fighting the car forever.

These are the things that cost me time.

### There are 2 states of charge

A raw pack figure and the displayed one, and they differ by roughly 10% because the displayed scale hides the buffer at both ends.
Reading the wrong one puts the limit in the wrong place.
The limiter reads raw by default and the source is configurable.

### Deferring with the ignition on does not look like it worked

The car keeps drawing from the wall to hold the pack topped up without raising SOC, by an amount that tracks the climate settings, and the charging flags read as an active charge throughout.
Anything watching those flags concludes the defer failed and retries forever.

### Replugging after a drive

Replugging after a drive can leave the car deferred in the HMI when the intent was to charge.
That needed an explicit override, which then needed an expiry, since an override that survives forever is just a disabled limiter.
It clears after 0.2 km of driving.

### The geofence

Limiting to 80% at home is useful.
Limiting to 80% at a public charger on a road trip is not.

It only applies at a configured location, and it is deliberately not enforced at all without a trustworthy GPS fix (such as underground parking), because home and a public charger are indistinguishable without one and the wrong failure is the one that strands you.

## August 9 to 11: negative results

These are the ones worth writing down.

### Charge history

PIDs `2243CB` through `2243CF` return 6 bytes where the only available decoder expects 4, and decoding them that way produces figures that contradict each other and the car.
Not resolved.

### Resetting the drive cycle counters

The since-last-charge figures cannot be reset on demand.
The car clears them when it reaches 100% and it does that internally, with nothing on the bus to copy.
Confirmed by capturing the bus across an actual 100% charge and finding nothing.

### Heated steering wheel

Not on either bus.
I tried every plausible source blind.
It behaves like a switch wired to a relay, and the fact that some cars have one and some do not is not visible anywhere either.

### Heated seats

These do work.
Both seats, all 3 levels, set directly rather than stepped.

Rear seats that do not physically exist accept the command silently and do nothing, which makes fitment undetectable that way.

## The alarm

Sitting in the car without the keys, doors locked, playing with the HMI, seemed harmless.
Opening the door set the alarm off.

Unlocking from OVMS does not silence a triggered alarm.
Nothing on the bus does.

:warning: The key fob is the only thing that stops it!

Pulling the 12 volt battery does stop it, and restarts every module in the car.
The charge port then loses its pilot signal until the cable is unplugged and replugged.

## August 7 to 12: Home Assistant

MQTT over TLS, with a private certificate authority whose root is good for 10 years.
It does 2 jobs: it encrypts the traffic, and it proves the broker is the real one rather than anything that answers on port 8883.
The module logs in with a username and password on top of that.

The discovery configs are generated from the OVMS metric declarations, not from a dump of live values.
This matters more than it sounds: at any given moment about half the metrics are empty, an empty value reads as text, and a text sensor has no state class, so Home Assistant silently never records statistics for it.
Generating from a dump produces a dashboard that looks correct and has no history a week later.

Two Home Assistant behaviors cost me an evening each:

- `suggested_display_precision` is read once, at first registration, and ignored forever after. Changing it on an existing entity has to go through the entity registry API.
- Entity IDs are assigned at first registration from the device name and are never renamed. The entity is `lock.chevrolet_volt_doors`, not whatever the config says today.

Then a cleanup.
After a real drive with the engine run deliberately, I removed every metric that never populated, from the dashboard and from the firmware both.

## August 14 to 18: range

EV range comes from `0x176`, and checks out against the DBC arithmetic and the cluster.

Gasoline range comes from `0x224`, and the DBC is misleading about it.
There is a validity bit next to the field, and gating on it produces nothing, because on this car it never asserts.
Not through a bus wake, not through a remote climate run, not through a drive.
The field itself is the honest signal: it reads 0 until the car is switched on, then about 5 seconds later it carries a figure. The engine does not have to run.

I verified it over one deliberate gasoline drive in Hold mode: 515 km on a full tank, drifting to 519 mid-drive as the estimate adapted, 471 km at the end with 4.25 L burned, matching the dash exactly.

Before that drive, an exhaustive search for a documented Gen 2 gasoline distance-to-empty PID came back empty.
It is not in any public source.
This is the only place it is written down.

Resolution is whole kilometers.
Confirmed by watching it count down one at a time on flat straight road under cruise control.

## August 12 to 19: the windows

The single longest problem, and the one with the most wrong answers along the way.

Window position is broadcast on `0x325`.
It is correct while the car is awake and for a short time afterwards.
At rest the frame carries a fixed idle pattern, `28 2d`, which is byte identical whether every window is shut or every window is down.
I captured it both ways to be sure.

So position at rest is not stale, not encoded differently, and not recoverable.
It is simply not transmitted.
I tried 4 different ways before having something reliable, each of which explained the observations up to the point where it did not.

The idle pattern is now ignored entirely rather than interpreted, and the only thing seeded at startup is a closed state, because that is the safe assumption for a parked car and not a claim about what the bus said.

The second half of the problem was reliability.
Commands worked roughly half the time, with the failures looking random: sometimes one window moved, sometimes 3, sometimes all 4 on the second press.
The cause was the bus being asleep, and the fix is to wake the bus and the BCM by flashing the interior lights unconditionally before commanding rather than trying to detect whether a wake is needed.

## August 19 to 20: the parts that are not the car

### Duplicate notifications

A single charge produced 3 `charge.started`, 1 custom message and 2 `charge.stopped`.
The framework already notifies on every `v.c.state` transition, and the component was also calling the notifier by hand.
Removing the manual calls fixed it.
The Bolt component appears to have the same bug.

### The controls page

Rebuilt so that every control is available regardless of state and the state is shown rather than implied, since a button that disappears when you need it is worse than one that might be a no-op.

## What it cost

Of the 4 weeks, the charge limit took about a week, the windows took about a week, Home Assistant took 3 days, and everything else fit around it.

The 2 things that consumed the most time were both cases of trusting a source over the car: the DBC's window fields, and the DBC's gas range validity bit.
Neither is wrong exactly.

The DBC describes the platform, not a car: it carries entries for diesel engines, trailers and rear seat massage.
A signal being defined in it says nothing about whether this car implements it.
