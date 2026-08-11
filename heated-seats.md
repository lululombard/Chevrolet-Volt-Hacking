# Heated seats and heated steering wheel (Gen 2 Volt, SWCAN)

Notes on reading and controlling the front heated seats over the single-wire bus, and on why the heated steering wheel cannot be touched at all.

Everything here was captured on my 2017 Chevrolet Volt (Gen 2) on the GMLAN low-speed single-wire bus (SW-CAN, 33.3 kbps, `can4` on OVMS).
All of it is observed on-car, not inferred from a DBC.

Signal layouts come from `gm_global_a_lowspeed.dbc`.
GMLAN 29-bit IDs here are `priority(3) | PID(13) | source(13)`, so match a message regardless of sender with:

```
((MsgID >> 13) & 0x1FFF) == <PID>
```

## What works

| | result |
|---|---|
| Front heated seats, read | works |
| Front heated seats, control | **works** |
| Heated steering wheel, read | not possible, no bus presence |
| Heated steering wheel, control | not possible |
| Detect what is fitted | works, via the control message |

## The 2 seat messages

They come from **different modules**, which is the key to the whole thing.

| PID | name | arb id observed | source | dlc | role |
|---|---|---|---|---|---|
| 0x392 | `Front_Seat_Heat_Cool_Switches` | `10724099` | 0x099 | 1 | button press events |
| 0x391 | `Front_Seat_Heat_Cool_Control` | `10722040` | 0x040 | 4 | mode + level indication |

Both are event-driven, sent only on change.

:warning: Never read "not present in a capture" as "not fitted" without a long observation window across several vehicle states!

### 0x392, the switches

One byte of momentary press flags, held roughly 110 ms then cleared.

| bit | signal | meaning |
|---|---|---|
| 0 | `DrvHCSeatSw1Act` | driver, heat button |
| 1 | `DrvHCSeatSw2Act` | driver, cool button |
| 2 | `DrvHCSeatSw3Act` | unused on this car |
| 3 | `PassHCSeatSw1Act` | passenger, heat button |
| 4 | `PassHCSeatSw2Act` | passenger, cool button |
| 5 | `PassHCSeatSw3Act` | unused on this car |

This car has a single cycling button per seat, so only `Sw1Act` ever fires in normal use.
It reports *that a button was pressed*, never a level.

### 0x391, the indication

It carries 4 bytes: `[drv mode][pass mode][drv level][pass level]`.

Mode byte: `0x0C` = heat (`MIndReq=1`, `MInd1`), `0x0A` = cool (`MInd2`), `0x00` = off.

Level byte, bits 4..0 are `Lev1`..`Lev5`, and the count of bits set is the number of bars on the display.
They fill Lev3 -> Lev2 -> Lev1:

| value | bits | bars |
|---|---|---|
| `0x3C` | Lev1+Lev2+Lev3 | 3 |
| `0x2C` | Lev2+Lev3 | 2 |
| `0x24` | Lev3 | 1 |
| `0x00` | none | off |

`Lev4`/`Lev5` never set on this car, i.e. 3 levels rather than 5.

## Control

Transmit a button press on 0x392 and let the real module do the work.
Send the press, then the release:

```
can can4 tx extended 10724099 01     # driver press   (08 for passenger)
can can4 tx extended 10724099 00     # release
```

The seat module answers on 0x391 within about 110 ms with exactly the payload a physical press produces.
I verified the full cycle 3 -> 2 -> 1 -> off, on both seats.
No diagnostic session, no SecurityAccess, no release frame, nothing latched.

:warning: Do not transmit 0x391! It is indication, emitted by a different module, and sending it would at best move the display without heating anything.

The other thing that is easy to get wrong is that control here is relative, not absolute.
It is a button press, so the level you land on depends on the level you started from.
To reach a known level, either read 0x391 first and count presses, or press 4 times to force off and then step up.

`0x3D2 RmStrHtdStEnRq` is not needed at all, despite being the obvious-looking candidate.

## Detecting what the car is fitted with

Use the control message, never the switch message.

| message | proves |
|---|---|
| 0x392 / 0x3B6 (switches) | nothing at all |
| 0x391 / 0x3B4 (control) | the equipment exists |

The switch panel broadcasts the whole family regardless of fitment.
On a car with **no** rear heated seats, `0x3B6 Rear_Seat_Heat_Cool_Switches` is still transmitted, while `0x3B4 Rear_Seat_Heat_Cool_Control` never appears.
Presence of a switch message means nothing, presence of a control message means a module exists to send it.

I confirmed this actively as well.
Transmitting a rear-seat press (`1076C099 01`/`00`) on a car without rear seats produced no response, no error and no cluster complaint, while a front press in the same capture answered in 50 ms.
Firing at absent hardware is harmless.

For trim level, use values rather than presence.
`Lev4`/`Lev5` never setting means 3-level seats, and the cool mode code never appearing passively means no ventilated seats.
The cool code *can* be induced artificially by sending `Sw2Act`, so only trust it as a passive signal.

## What about the heated steering wheel?

It is not on the bus, so there is nothing to read and nothing to control.

The DBC suggests `0x390 High_Volt_Climate_Pwr_Status_LS` (`HtdStWhlCmd` byte0 bits 2-1, `HtdStWhlInd` bits 4-3, `HtdStWhlCtrlSrc` bit 5) and `0x30C MnlHtdStWhlRqstd` (byte0 bit 7).
The DBC promises 2 ways in and the car delivers neither:

- 0x390 *is* broadcast (`10720099`, dlc 5), but byte0 stayed `0x00` through a real physical press, across 16 frames over 38 s with zero drops
- 0x30C is never broadcast at all
- a blind transmit of 0x30C bit 7 against every source byte observed on this car, at priority 4, did nothing
- an RE diff of all 200+ can1 IDs between wheel-on and wheel-off found nothing wheel-specific

The only traces are indirect electrical load: `PrpDspTtlPwrLvlPct` on 0x17A swinging +0.78% -> -1.57%, and the 0x390 climate power estimate wobbling with a burst of fast updates around the press.
Best explanation is that the heated wheel is a plain switch latching a relay onto a heating element, with no module and no bus presence.

Useful side catch: 0x390 also carries `ClntHtrElecPwrReq` (byte1), `EstACCompPwrRchCbnCmf` (byte2) and `EstACCompPwrMtnCbnCmf` (byte3), all 0.04 kW per LSB.
Real climate power telemetry, free from the same handler.
Note 0x390 is dormant unless climate power is actually being drawn.

## Method notes

- `can log start vfs crtd /sd/x.crtd <filters>` is reliable when filtered to specific PID ranges and drops nothing
- unfiltered it drops around 39% of frames on SW-CAN, and on can1 at 500 kbps it kills the logger outright and leaves a zero-byte file
- filter syntax is `<bus>:<from>-<to>` in hex on the full arbitration ID
- since PID sits at bits 13..25, one range per priority covers a PID, for example PID 0x391 at priority 4 is `4:10722000-10723fff`
- contiguous PIDs can share a range
- `re start` / `re list` diffing is the right tool for hunting an unknown ID

:warning: Check `re status` before trusting any result! The tool can stop on its own, and an empty capture diffs to a clean looking but meaningless negative.

Absence is only evidence once you have proven the channel was live.
Confirm the message is being transmitted at all before concluding a signal within it never changes.
