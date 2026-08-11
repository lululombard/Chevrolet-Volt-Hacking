#!/usr/bin/env python3
"""Build a metric name -> (type, unit) table from the OVMS source.

Typing entities from whatever value a metric happens to hold at dump time is unreliable:
roughly half of them are empty on any given dump (event driven, or only populated while the
car is awake), and an empty value looks like text. That produced text sensors for booleans and
untyped sensors for numbers, so Home Assistant would not record statistics or draw a proper
binary state.

OVMS declares every standard metric with its concrete type and unit, so read that instead.
metrics_standard.h maps a constant to a dotted name, metrics_standard.cpp maps the same
constant to an OvmsMetric<Type> and a unit enum.
"""
import re, sys

TYPE_MAP = {
    "OvmsMetricBool": "bool",
    "OvmsMetricInt": "number", "OvmsMetricInt64": "number",
    "OvmsMetricFloat": "number",
    "OvmsMetricString": "text",
    "OvmsMetricBitset": "text", "OvmsMetricSet": "text", "OvmsMetricVector": "text",
}

# OVMS unit enum -> (HA unit, HA device_class)
UNIT_MAP = {
    "Percentage": ("%", None), "Volts": ("V", "voltage"), "Amps": ("A", "current"),
    "AmpHours": ("Ah", None), "kW": ("kW", "power"), "kWh": ("kWh", "energy"),
    "Watts": ("W", "power"), "WattHours": ("Wh", "energy"),
    "Celcius": ("°C", "temperature"), "Kilometers": ("km", "distance"),
    "Meters": ("m", "distance"), "Kph": ("km/h", "speed"), "Seconds": ("s", "duration"),
    "Minutes": ("min", "duration"), "Hours": ("h", "duration"), "kPa": ("kPa", "pressure"),
    "Degrees": ("°", None), "dbm": ("dBm", "signal_strength"), "sq": (None, None),
    "WattHoursPK": ("Wh/km", None), "kWhP100K": ("kWh/100km", None),
    "KPkWh": ("km/kWh", None), "Nm": ("Nm", None), "MegaJoules": ("MJ", None),
    "Kilocoulombs": ("kC", None), "Other": (None, None), "Native": (None, None),
}


def build(src_dir):
    names = {}
    for m in re.finditer(r'^#define\s+(MS_\w+)\s+"([^"]+)"',
                         open(f"{src_dir}/metrics_standard.h", errors="replace").read(),
                         re.M):
        names[m.group(1)] = m.group(2)

    out = {}
    for m in re.finditer(r'new\s+(OvmsMetric\w+)\s*\(\s*(MS_\w+)\s*(?:,\s*\w+\s*)?'
                         r'(?:,\s*(\w+)\s*)?',
                         open(f"{src_dir}/metrics_standard.cpp", errors="replace").read()):
        cls, const, unit = m.group(1), m.group(2), m.group(3)
        if const not in names or cls not in TYPE_MAP:
            continue
        out[names[const]] = (TYPE_MAP[cls], unit or "Other")
    return out


if __name__ == "__main__":
    t = build(sys.argv[1])
    print(f"# {len(t)} standard metrics")
    for k in sorted(t):
        print(f"{k}\t{t[k][0]}\t{t[k][1]}")
