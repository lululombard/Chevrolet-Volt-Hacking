# Battery capacity, health and consumption on a Volt

The BECM reports pack capacity in amp-hours and nothing else, so every kWh figure here (usable energy, state of health, consumption) is that one measured number times a per-model constant.

Those constants come from decompiling the Voltage Android app, not from guesswork.
I guessed first and every guess was wrong, so the failures are written down further below because they are the useful part.

## Reading capacity

Two PIDs carry it, both on node `7E4`:

| PID | Decode | Applies to |
|---|---|---|
| `22 41 A3` | `uint16 / 10` → Ah | 2011-2018 |
| `22 45 FF` | `uint16 / 100` → Ah | 2019+ |

There is no model gating.
Both are polled on every car and whichever answers wins, and where both answer, `41A3` takes precedence.
Voltage resolves it the same way, by simply letting `41A3` overwrite.
So "2019+" is emergent from which DID responds, not from the VIN.

## State of health

```
SoH % = measured_Ah / nominal_Ah × 100
```

`nominal_Ah` is a per-model-year constant.
GM changed cell capacity across the Gen 1 run, so a single Gen 1 figure misreports an older car by up to 10%:

| Model | nominal Ah |
|---|---|
| Volt / Ampera '11-12 | 45.0 |
| Volt / Ampera '13-14 | 48.0 |
| Volt / Ampera '15, ELR '16 | 50.2 |
| Volt '16-19 (Gen 2) | 51.8 |
| Spark EV | 52.0 |
| Bolt / Ampera-e | 196.0 |

I checked it on a 2017 reporting 41.8 Ah: `41.8 / 51.8 = 80.7%`, which matches what Voltage reports for the same car.

## Usable energy in kWh

```
kWh = measured_Ah × nominal_voltage / 1000
```

`nominal_voltage` is not the pack's electrical nominal.
It is a scaling constant chosen so that the product lands on the energy available for driving:

| Model | nominal V |
|---|---|
| Gen 1 Volt / Ampera / ELR (MY ≤ 15) | 230 |
| **Gen 2 Volt (MY ≥ 16)** | **297** |
| Bolt / Spark EV | 320 |

No separate buffer fraction is applied on top.
The constant already accounts for it.

The Gen 1 value is exact against GM's published figure: `45.0 Ah × 230 V = 10.35 kWh` versus 10.3 kWh usable.
The Gen 2 value sits a little above GM's stated 14.0 kWh but matches what owners measure on aged packs.
The 2017 above gives `41.8 × 297 = 12.4 kWh`.
I measure 12.4 kWh usable running the pack down until the engine starts, so the constant and the car agree exactly.

:warning: jadx decompiles this function wrongly!
Its default output inverts the Bolt test in the voltage selector, so the naive reading maps the constants to the wrong vehicles.
Use `jadx -m simple` or `-m fallback`, or hand-decode the DEX.
I verified it three ways.

## Consumption

The car reports charge-cycle economy as km per liter of gasoline-equivalent, which is MPGe in metric clothing.
Convert with the EPA equivalence that defines MPGe, 33.7 kWh per US gallon:

```
kWh/100km = 100 / (km_per_litre_equivalent) × 8.9
```

Sanity check on a real trip: 26.7 km/L-e converts to 33 kWh/100 km, against 39 kWh/100 km derived independently from the SOC drop over the same 6 km.
On a trip that short, 1% of SOC is worth about 2 kWh/100 km, so the two agree inside the error.

## SOC is not as solid as it looks

The BECM's own estimate drifts while the car rests.
I have seen it gain 7% overnight, parked and unplugged, with no charge in the log.
It re-converges after an interrupted charge and it is temperature-compensated.

Consequences worth knowing:

- a charge limit is only ever as precise as this number
- the displayed 0% is not an empty pack, the usable window is roughly 16%-96%
- comparing a capacity figure against a SOC-derived one needs generous error bars

## What I got wrong on the way here

Keeping these here so nobody has to redo them.

### Deriving kWh from the electrical nominal

I used 96 cells × 3.7 V = 355 V.
Plausible, and about 10% high.
Worse, I paired it with a hand-picked 0.80 buffer, which produced an effective 284 V that looked right on a Gen 2 and was 24% wrong on every Gen 1.
Two errors nearly cancelling, which is the most convincing way to be wrong.

### Assuming `cac` was the usable window

It is the gross pack.

### Trusting PID adjacency

`223040` is an EGR cooler, not motor temperature.
`2241Ax` is the trip computer, not battery thermals.
Adjacent PIDs are not related subsystems.
Only decoder traces and live probing were reliable.

## Where the energy went, as a share of the pack

`Drv_Cycl_Elec_Enrgy_Consumd_LS` (PID `0x0210`) carries four percentages, `DCEEC_EngyPct1` to `4`, at bytes 2 to 5.
The DBC gives them no names, no comments and no value tables, so what they mean has to come from the car.

They are shares of the whole pack since the last full charge, not shares of the energy consumed:

| byte | meaning | measured |
|---|---|---|
| 2 | used for driving | 16.86% |
| 3 | used for climate | 1.96% |
| 4 | used for other | 0% |
| 5 | still available | 81.18% |

The car's energy screen shows the first three, rounded to whole percent, as 17 / 2 / 0.
They do not add up to 100 and that confuses people, [including on Reddit](https://www.reddit.com/r/volt/comments/cf2ptq/gen2_2018_volt_energy_details_often_dont_add_up/), where the explanation came from.
The balance is the fourth value, the charge you have left, which the screen never displays.
The four raw counts total exactly 255.

Two checks that this reading is right:

* `pct4` was 81.18% against a displayed SOC of 81.6%, a difference smaller than one 0.392% count.
* The 2.3 kWh that had been used was 18.82% of the pack, implying 12.22 kWh usable, against 12.44 kWh derived independently from `cac` and pack voltage above. Within 1.5%.

So the split doubles as a sanity check on the capacity model, and vice versa.

## More info

Every constant in here came out of the Voltage Android app, so the credit for them goes to thanxx.
The percentage split would still be a mystery without the Reddit thread linked above.
