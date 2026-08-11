#!/usr/bin/env python3
"""Turn OVMS metric paths into readable entity names.

OVMS metric names are terse dotted paths (v.b.12v.soc, xva.v.ac.evap_temp). Shown raw in
Home Assistant they are unreadable, so this expands them using OVMS' own namespace
conventions plus an abbreviation dictionary.
"""
import re

# A few paths where segment-by-segment expansion reads badly however you tune it.
FULL = {
    # Shares of the WHOLE pack since the last full charge, not shares of the energy consumed.
    # The car's energy screen shows the first three and they deliberately do not add up to 100:
    # the balance is pct4, the charge still available, which the screen never displays. The four
    # raw counts total exactly 255. Confirmed on car: display 17 / 2 / 0 against 16.86 / 1.96 /
    # 0, and pct4 81.18% against a displayed SOC of 81.6%, inside one 0.392% count.
    "xva.v.dc.energy.pct1": "Drive cycle battery used driving",
    "xva.v.dc.energy.pct2": "Drive cycle battery used climate",
    "xva.v.dc.energy.pct3": "Drive cycle battery used other",
    "xva.v.dc.energy.pct4": "Drive cycle battery remaining",
    "xva.v.m.temp.mga": "Motor-generator A temperature",
    "xva.v.m.temp.mgb": "Motor-generator B temperature",
    "xva.v.m.temp.trans": "Transmission temperature",
    "xva.v.p.trip.ev": "EV trip",
    "xva.v.b.power.disp": "Displayed battery power",
    "v.c.charging": "Charging",
    "v.g.generating": "Generating",
    "v.type": "Vehicle type",
    "m.obdc2ecu.on": "OBD2ECU active",
}

# Top-level namespaces -> the prefix that makes the rest read as English. Longest match wins,
# so the three-segment entries below take precedence over their two-segment parents.
NAMESPACE = {
    ("m",):        "Module",
    ("m", "net"):  "Network",
    ("s",):        "Server",
    ("v", "b"):    "Battery",
    ("v", "b", "c"): "Battery cell",
    ("v", "b", "p"): "Battery pack",
    ("v", "e", "c"): "",         # v.e.c.config / v.e.c.login read fine bare
    ("v", "c"):    "Charge",
    ("v", "d"):    "Door",
    ("v", "e"):    "",          # environment: usually reads fine unprefixed
    ("v", "g"):    "Generator",
    ("v", "i"):    "Inverter",
    ("v", "m"):    "Motor",
    ("v", "p"):    "Position",
    ("v", "t"):    "Tyre",
    ("xva", "v", "ac"):  "A/C",
    ("xva", "v", "b"):   "Battery",
    ("xva", "v", "c"):   "Charge",
    ("xva", "v", "e"):   "Engine",
    ("xva", "v", "m"):   "Motor",
    ("xva", "v", "p"):   "Trip",
    ("xva", "v", "dc"):  "Drive cycle",
    ("xva", "stat"):     "Stats",
}

# Segment expansions. Values already cased the way they should appear.
WORD = {
    "soc": "SoC", "soh": "SoH", "cac": "capacity (Ah)",
    # Drive cycle ("since last charge") vocabulary
    "dist": "distance", "eff": "efficiency", "econ": "economy", "batt": "battery",
    "own": "measured by OVMS", "st5": "alternate reading", "avg": "average",
    "ratio": "share", "inhibit": "inhibit reason", "life": "life",
    "fuelfilter": "fuel filter", "oil": "oil", "displayed": "displayed", "raw": "raw",
    "lifetime": "lifetime", "input": "input", "hist1": "history 1", "hist2": "history 2",
    "hist3": "history 3", "hist4": "history 4", "hist5": "history 5",
    "temp": "temperature", "temps": "temperatures",
    "pwr": "power", "pct": "percent", "volt": "voltage", "amp": "current",
    "12v": "12V", "hv": "HV", "ac": "AC", "dc": "DC",
    "fl": "front left", "fr": "front right", "rl": "rear left", "rr": "rear right",
    "cp": "charge port", "hood": "hood", "trunk": "trunk",
    "mga": "motor-generator A", "mgb": "motor-generator B", "trans": "transmission",
    "rpm": "RPM", "kwh": "kWh", "km": "km", "gps": "GPS", "gpslock": "GPS lock",
    "gpstime": "GPS time", "gpsmode": "GPS mode", "gpsspeed": "GPS speed",
    "gpshdop": "GPS HDOP", "gpssq": "GPS signal quality", "satcount": "satellite count",
    "sq": "signal quality", "mdm": "modem", "iccid": "ICCID", "imei": "IMEI",
    "netreg": "network registration", "freeram": "free RAM", "egpio": "EGPIO",
    "tasks": "tasks", "monotonic": "uptime", "hardware": "hardware", "serial": "serial",
    "vin": "VIN", "obdvin": "OBD VIN", "evap": "evaporator", "econ": "economy",
    "disp": "displayed", "req": "requested", "used": "used", "recd": "recovered",
    "est": "estimated", "ideal": "ideal", "full": "full", "min": "minimum",
    "max": "maximum", "dev": "deviation", "alert": "alert", "ref": "reference",
    "coolant": "coolant", "heatercore": "heater core", "chargecycle": "charge cycle",
    "climit": "current limit", "duration": "duration", "kwh_grid": "kWh from grid",
    "inprogress": "in progress", "substate": "substate", "timermode": "timer mode",
    "timerstart": "timer start", "limit": "limit", "range": "range", "mode": "mode",
    "type": "type", "state": "state", "power": "power", "current": "current",
    "voltage": "voltage", "capacity": "capacity", "health": "health", "level": "level",
    "speed": "speed", "odometer": "odometer", "trip": "trip", "direction": "direction",
    "altitude": "altitude", "latitude": "latitude", "longitude": "longitude",
    "acceleration": "acceleration", "gpstime_utc": "GPS time UTC",
    "charging": "charging", "pilot": "pilot", "chargeport": "charge port",
    "awake": "awake", "on": "on", "locked": "locked", "valet": "valet",
    "headlights": "headlights", "alarm": "alarm", "handbrake": "handbrake",
    "footbrake": "foot brake", "throttle": "throttle", "gear": "gear",
    "cooling": "cooling", "heating": "heating", "hvac": "climate",
    "cabintemp": "cabin temperature", "cabinfan": "cabin fan",
    "cabinsetpoint": "cabin setpoint", "cabinvent": "cabin vent",
    "charging12v": "12V charging", "ctrl": "controller", "login": "login",
    "connected": "connected", "peers": "peers", "fuel": "fuel",
    "blower": "blower", "front_blower_fan_speed": "front blower fan speed",
    "compressor_rpm": "compressor RPM", "hv_power_limit": "HV power limit",
    "hv_power_req": "HV power requested", "coolant_temp": "coolant temperature",
    "coolant_heater_pwr": "coolant heater power", "heater_pwr": "heater power",
    "charging_limits": "charging limits", "preheat": "remote start",
    "preheat_timer": "remote start timer", "active": "active", "heater": "heater",
    "energy": "energy", "gross": "gross", "ev": "EV",
    # network / module
    "net": "network", "ip": "IP", "wifi": "WiFi", "utc": "UTC", "sq": "signal quality",
    # statistics suffixes
    "avg": "average", "stddev": "std deviation", "grad": "gradient",
    # compounds OVMS writes as one word
    "aux12v": "aux 12V", "cabinintake": "cabin intake", "drivemode": "drive mode",
    "drivetime": "drive time", "parktime": "park time", "regenbrake": "regen brake",
    "serv": "service", "obdc2ecu": "OBD2ECU",
}

KEEP_UPPER = {"SoC", "SoH", "HV", "AC", "DC", "RPM", "GPS", "VIN", "ICCID", "IMEI",
              "EGPIO", "RAM", "kWh", "12V", "EV", "A/C", "HDOP", "OBD", "P"}


def expand(seg):
    if seg in WORD:
        return WORD[seg]
    # split things like evap_temp / hv_power_req that are not in the dict wholesale
    if "_" in seg:
        return " ".join(expand(p) for p in seg.split("_"))
    return seg


def friendly(metric):
    if metric in FULL:
        return FULL[metric]
    parts = metric.split(".")
    prefix = ""
    rest = parts
    for n in (3, 2, 1):
        key = tuple(parts[:n])
        if key in NAMESPACE:
            prefix = NAMESPACE[key]
            rest = parts[n:]
            break
    else:
        if parts[0] == "v":
            rest = parts[1:]

    words = []
    for seg in rest:
        e = expand(seg)
        if e:
            words.append(e)
    body = " ".join(words).strip()

    name = (prefix + " " + body).strip() if prefix else body
    if not name:
        name = metric

    # Sentence case, but never lowercase an acronym we deliberately cased.
    toks = name.split()
    out = []
    for i, t in enumerate(toks):
        if t in KEEP_UPPER or t.upper() == t and len(t) > 1:
            out.append(t)
        elif i == 0:
            out.append(t[0].upper() + t[1:])
        else:
            out.append(t)
    return " ".join(out)


if __name__ == "__main__":
    import sys
    for line in open(sys.argv[1]):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        m = re.split(r"\s{2,}", line.strip(), maxsplit=1)[0].strip()
        if re.match(r"^[a-z0-9_.]+$", m):
            print(f"{m:38s} -> {friendly(m)}")
