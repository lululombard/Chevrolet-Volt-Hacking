# Charge limit

The Volt has no charge limit setting and no "stop charging" command, so this is how I cap it anyway.

What it does have is departure based charging, where you tell it when you are leaving and it works out when to start.
That is the lever.

## How it works

Put the car into departure mode with a departure time far enough away that it decides not to charge yet.
It stops.
When you want it to charge again, put it back into immediate mode.

The complication is that the car does not stay put.
It goes back to charging on its own, so a single write is not enough.
You have to watch for that and reapply, which is why this belongs in something always powered and on the bus rather than in a phone app.

Credit where it is due: the Voltage app (`io.tripovan.voltage`) does this, and its frames are where the sequence below came from.
It works, but it can only act while your phone is nearby and awake, which is exactly the limitation that makes an always on module a better home for it.

## The frames

All of this is GMLAN service `3B` (WriteDataByIdentifier) to node `0x7E4`, the HPCM, on the single wire bus.
Single frame ISO-TP, so on raw CAN you send `[length][3B][DID][data...]` padded to 8 bytes.

Immediate charging:

```
3B 77 02 00 00        (wait 1000 ms)
3B 76 01 01
```

Departure charging:

```
3B 76 02 01           (wait 1000 ms)
3B 77 02 <slot> 00
```

`slot` is a 15 minute slot number, `hour * 4 + minute / 15`, so 23:45 is 95.

## Why a bare write does not work

Writing the departure frames on their own gets a positive response and changes nothing.
The car acknowledges it and keeps charging.
I lost an evening to this at 88% state of charge before working out what the difference was.

The sequence that does work spoofs the vehicle clock first:

1. Wake the bus
2. Set the vehicle clock to 12:00 (node `0x244`, `3B 30 <hour>` and `3B 31 <minute>`)
3. Send the immediate charging frames
4. Send the departure frames for 23:45
5. Restore the real clock

The clock spoof makes the departure time look far away at the moment the car evaluates it.
The real clock going back afterwards does not undo the decision.
Yes, you have to lie to the car about what time it is.
This is what Voltage does too.

## Which percentage do you cap?

The car reports state of charge on 2 different scales and they are not the same number.

| PID | what it is | resolution |
|---|---|---|
| `22 83 34` | the dashboard number, 8 bit | 0.39% |
| `22 43 AF` | the pack's own figure, 16 bit | 0.0015% |

Neither is derived from the other, they are separate reads.
Measured pairs on my car, dashboard against raw:

```
20.0 / 32.4     50.2 / 56.8     78.8 / 79.4     95.3 / 92.7     99.6 / 99.9
```

Raw sits well above the dashboard when the battery is low, tracks it closely around 80, drops below it through the high 80s and 90s, then crosses back at the very top.
It is not a linear remap of a window, so do not try to convert one into the other.

For a charge limit, the raw figure is the right one, since capping the charge is a decision about the pack rather than about what the dashboard chooses to show.
A target of 70 measured against raw stops somewhere around 67 on the dashboard.

The raw value also does something odd at the top of a charge.
It climbed to 99.9%, then fell back to around 96 over the following 10 minutes while the car was still drawing about 1 kW from the wall with almost no current going into the pack.
Best guess is the battery management system recalibrating against a known full point, but I have not confirmed that.

## Things that will bite you

### The charging flags while the car is switched on

Plugged in and powered up, the car draws from the charger to run itself and keep the pack topped up without raising SOC.
How much it draws depends on what the climate system is doing, so do not key off a particular figure.

:warning: A defer applied in this state works, but the charging flags read as an active charge the whole time regardless, so do not trust them!

Anything treating those flags as proof the defer failed will reapply it every few seconds, burn through its retry budget in minutes, and give up with a false alarm.
Apply at most one defer while the car is on, never retry on the flags, and leave resuming until the car is off.

### Hysteresis on the resume

Keep a margin, because a defer sitting exactly on target looks stale the moment a reading lands a fraction under it.

### Departure mode outlives a reboot, your record of it does not

The car keeps the state, anything tracking it in RAM does not, so after a restart you can find the car deferred with nothing that knows why.
Recover from it, but only when the charge level is clearly below target, otherwise you will fight a departure time you set yourself.

### Geofence it

:warning: Capping the charge at a public charger is worse than not capping it at all, so do not enforce without a reliable position fix!

## Reading back what the car thinks

`1A 76` on `0x7E4` returns whether the car is in departure mode.
Useful for telling a defer that worked from one that did not, and for spotting a stale one after a restart.

`22 43 7D` returns the energy taken from the wall for the current or last charge, in 10 Wh steps.
Wall side, so it includes charger and cooling losses and reads higher than the change in pack charge.
It matched a metered session to within a rounding error.

## The charge history PIDs are a dead end

`22 43 CB` through `22 43 CF` look promising.
Voltage calls them Charging History 1 to 5, and it decodes them as a 4 byte bitfield of stop reasons (full charge, time of day inhibit, unplugged, various faults).
If that worked it would tell you directly why a charge ended, which is exactly what you want when a charge limit stops one.

It does not work on a Gen 2.
The car answers with **6** bytes, not 4, and read as that bitfield the records are self contradictory.
One of mine decodes as "full charge" and "time of day inhibit" and "unplugged at wall" and both multi plug in flags, all at once.

Decompiling the app settles why:

- There is exactly one code path for all 5 PIDs, in a single function, with no model year or generation branch, even though the same file branches on generation for other PIDs
- The app never reads bytes 4 or 5, on any PID: the only byte indices used anywhere in the entire codebase are 0 to 3
- It stores all 6 and silently discards the last 2, which are the ones that look most informative

So the decoder is presumably correct for some other GM platform and was never revisited.
Nothing more can be recovered from the app, because the app does not know either.

What the 6 bytes actually mean is still open.
Across 5 samples, byte 2 was always `0x01` and byte 5 always `0xfe` (254, which is 99.6% on the SOC scale), while byte 4 moved between 0x40 and 0x58.
Cracking it needs a controlled charge: record start and end SOC on both scales, `22 43 7D`, charge level and duration, and read all 5 PIDs immediately before and after, on a charge with no defer, override or replug so exactly one event falls between the 2 reads.

## The OnStar frame, fully decoded

`0x1024E097` is `Telematics_Contol_LS` (PID `0x0127`, 3 bytes) in opendbc, and every field is pinned by frames that were already known to work.

| field | where | meaning |
|---|---|---|
| `EnhSrvRClsRlsRq` | byte0 `0x02` | trunk |
| `EnhSrvVisAlRq` | byte0 `0x0C` | flash lights |
| `EnhSrvAudAlRq` | byte0 `0x30` | horn |
| `EnhSrvRmStrtRq` | byte0 `0xC0` | remote start, 2 on / 1 off |
| `EnhSrvLckRq` | byte1 `0x07` | lock 1 / unlock 3 |
| `EnhSvVehTopSpdLim` | byte2 | top speed limiter, `0xFF` = none |

Checks: `0001FF` lock, `0003FF` unlock, `0200FF` trunk, `8001FF` remote start on, `4001FF` off, `0000FF` release.
All match.

This corrects something that had been in the code for years.
The `0C 00 FF` sent after every lock and unlock was documented as a "commit" step.
It is `EnhSrvVisAlRq = 3`, the visual confirmation flash.
Optional, not required.

Horn confirmed on-car:

```
can can4 tx extended 1024E097 30 00 ff     # horn      (3C = horn + lights)
can can4 tx extended 1024E097 00 00 ff     # release
```

One precondition: the bus must be awake, or the controller goes transmit error-passive with no node to ACK and every frame silently fails.

Unlike the heated seats this does not need the body powered.
The seat module only answers once the car itself is up, but the BCM acts on these alerts from a plain bus wake.

`EnhSvVehTopSpdLim` is deliberately not exposed.
It caps the car's speed, which is not something a button should be able to do.
