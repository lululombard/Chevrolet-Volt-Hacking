#!/usr/bin/env python3
"""Generate Home Assistant MQTT discovery configs for an OVMS vehicle.

OVMS publishes plain metric topics and has no HA discovery of its own, so this builds the
retained `homeassistant/.../config` messages that make HA create a proper device.

Output: one "<topic>\t<json>" line per entity on stdout, ready to feed to mosquitto_pub.
"""
import json, os, re, sys
from names import friendly
from metric_types import build as build_types, UNIT_MAP as OVMS_UNIT_MAP

VEHICLE   = "volt"
PREFIX    = f"ovms/{VEHICLE}/"
NODE      = f"ovms_{VEHICLE}"
CMD_CLIENT= "hass"

DEVICE = {
    "identifiers": [NODE],
    "name": "Chevrolet Volt",
    "manufacturer": "Chevrolet",
    "model": "Volt Gen 2 (2017)",
}
AVAIL = [{
    "topic": PREFIX + "metric/s/v3/connected",
    "payload_available": "yes",
    "payload_not_available": "no",
}]

# Display precision. Without this HA shows "47.00 km" for a value the car reports as a whole
# number, because it falls back to a generic 2 decimals for numeric sensors.
PRECISION = {
    "v.b.soc": 2, "v.b.soh": 1, "v.b.range.est": 0, "v.b.range.ideal": 0,
    "v.b.capacity": 2, "v.b.cac": 1, "v.b.voltage": 1, "v.b.current": 1, "v.b.power": 2,
    "v.b.temp": 0, "v.b.12v.voltage": 2,
    "v.c.limit.soc": 0, "v.c.kwh": 2, "v.c.power": 2, "v.c.current": 1, "v.c.voltage": 0,
    "v.e.temp": 0, "v.e.cabintemp": 1,
    "v.p.odometer": 0, "v.p.speed": 0, "v.m.rpm": 0, "v.m.temp": 0, "xva.v.e.fuel": 0,
    # The car computes both ranges in whole km (GCD of every raw step on 0x224 is 64, exactly
    # 1.0 km); decimals on the display would be an artifact of the wire encoding.
    "xva.v.e.range.fuel": 0, "xva.v.range.total": 0, "v.p.trip": 1, "xva.v.dc.fuel.econ": 1,
    # Drive-cycle figures: one decimal is the useful resolution, and the raw values carry far
    # more (trip consumption arrives as 41.5888 kWh/100km, which is noise past the first digit).
    "xva.v.dc.dist.total": 1, "xva.v.dc.dist.batt": 1, "xva.v.dc.dist.fuel": 1,
    "xva.v.dc.distance.own": 1, "xva.v.dc.energy.used": 1, "xva.v.dc.energy.own": 1,
    "xva.v.dc.fuel.used": 1, "xva.v.b.chargecycle_econ": 1, "xva.v.e.oil.life": 1,
    # Temperatures the car reports to a fraction it does not actually measure to.
    "v.b.temp": 0, "v.e.temp": 0, "v.e.cabintemp": 0, "xva.v.e.heatercore_temp": 0,
    "xva.v.ac.evap_temp": 0, "xva.v.m.temp.mga": 0, "xva.v.m.temp.mgb": 0,
}

# Metrics worth surfacing on the main device card. Everything else is still created, but
# tagged diagnostic so it lands in HA's separate section and stays out of auto-dashboards.
# (name, device_class, unit, state_class, icon)
CURATED = {
    "v.b.soc":            ("Battery",              "battery",     "%",    "measurement", None),
    "v.b.soh":            ("Battery health",       None,          "%",    "measurement", "mdi:heart-pulse"),
    "v.b.capacity":       ("Usable capacity",      "energy_storage","kWh","measurement", None),
    "v.b.cac":            ("Pack capacity",        None,          "Ah",   "measurement", "mdi:battery-heart-variant"),
    "v.b.range.est":      ("Range estimated",      "distance",    "km",   "measurement", None),
    "v.b.range.ideal":    ("Range ideal",          "distance",    "km",   "measurement", None),
    "v.b.voltage":        ("Pack voltage",         "voltage",     "V",    "measurement", None),
    "v.b.current":        ("Pack current",         "current",     "A",    "measurement", None),
    "v.b.power":          ("Pack power",           "power",       "kW",   "measurement", None),
    "v.b.temp":           ("Battery temperature",  "temperature", "°C",   "measurement", None),
    "v.b.12v.voltage":    ("12V battery",          "voltage",     "V",    "measurement", None),
    "v.c.limit.soc":      ("Charge limit",         None,          "%",    "measurement", "mdi:battery-lock"),
    "v.c.kwh":            ("Charged this session", "energy",      "kWh",  "total_increasing", None),
    "v.c.power":          ("Charge power",         "power",       "kW",   "measurement", None),
    "v.c.current":        ("Charge current",       "current",     "A",    "measurement", None),
    "v.c.voltage":        ("Charge voltage",       "voltage",     "V",    "measurement", None),
    "v.c.state":          ("Charge state",         None,          None,   None,          "mdi:ev-station"),
    "v.c.substate":       ("Charge substate",      None,          None,   None,          "mdi:ev-station"),
    "v.e.temp":           ("Outside temperature",  "temperature", "°C",   "measurement", None),
    "v.e.cabintemp":      ("Cabin temperature",    "temperature", "°C",   "measurement", None),
    "v.p.odometer":       ("Odometer",             "distance",    "km",   "total_increasing", None),
    "v.p.speed":          ("Speed",                "speed",       "km/h", "measurement", None),
    "v.m.rpm":            ("Engine RPM",           None,          "rpm",  "measurement", "mdi:engine"),
    "v.m.temp":           ("Motor temperature",    "temperature", "°C",   "measurement", None),
    "xva.v.e.fuel":       ("Fuel level",           None,          "%",    "measurement", "mdi:gas-station"),
    "xva.v.e.mode":       ("Engine override",      None,          None,   None,          "mdi:engine-outline"),
}

# Booleans worth a real binary_sensor on the main card.
# (name, device_class, icon, invert)
# invert=True swaps the payloads. HA's `lock` device class is defined the opposite way round
# from the metric: for HA, on = unlocked / off = locked, while OVMS publishes locked = yes.
CURATED_BINARY = {
    "v.c.charging": ("Charging",     "battery_charging", None,              False),
    "v.c.pilot":    ("Plugged in",   "plug",             None,              False),
    "v.e.on":       ("Car on",       "running",          None,              False),
    "v.e.awake":    ("Awake",        None,               "mdi:sleep-off",   False),
}

# Buttons. Engine control is deliberately absent: it can start a petrol engine, which is not
# something an automation or a mis-tap should ever be able to do.
# A lock entity rather than a pair of buttons: it carries state, so Home Assistant renders one
# control showing locked/unlocked and offering the opposite action. Two buttons cannot do that,
# they are stateless and always show both.
# {PIN} is substituted at generation time. The module refuses these outright without the
# vehicle PIN, so the payload has to carry it.
LOCKS = [
    ("lock", "Doors", "v.e.locked", "lock {PIN}", "unlock {PIN}", "mdi:car-door-lock"),
]

# Switches for the on/off pairs, same reasoning as the lock: they carry state, so Home Assistant
# renders one control that shows what the car is doing and offers the opposite action.
# (key, label, state metric, payload_on, payload_off, icon)
SWITCHES = [
    ("climate", "Climate", "v.e.hvac", "climatecontrol on", "climatecontrol off",
     "mdi:air-conditioner"),
    ("charging", "Charging", "v.c.charging", "charge start", "charge stop",
     "mdi:battery-charging"),
    ("charge_full_once", "Charge to 100% once", "xva.v.c.limit.override",
     "xva chargelimit override", "xva chargelimit resume", "mdi:battery-charging-100"),
    # The windows metric carries the two transitional states as well, so map each of them onto
    # the position they are heading for. The toggle then flips the moment the glass starts
    # moving rather than after it settles. An unmatched payload leaves the state untouched.
    ("windows", "Windows", "xva.v.e.windows",
     "xva windows down {PIN}", "xva windows up {PIN}", "mdi:car-door", {
        "state_on": "open",
        "state_off": "closed",
        "value_template": "{{ 'open' if value in ['open', 'opening'] else 'closed' }}",
     }),
]

BUTTONS = [
    # Disabled on arrival. Releasing the hatch remotely cannot be undone remotely: the car will
    # sit there unlatched until somebody physically shuts it. Enable it by hand if you want it.
    ("trunk",          "Trunk",               "xva trunk {PIN}",          "mdi:car-back",
     {"enabled_by_default": False}),
    ("wakeup",         "Wake car",            "wakeup",                   "mdi:bell-ring"),
    # OnStar telematics alerts. These need xva/control.enabled, and the car's body has to be
    # powered for the modules to act on them, same as the heated seats.
    ("horn",           "Horn",                "xva horn",                 "mdi:bugle"),
    ("flash",          "Flash lights",        "xva flash",                "mdi:car-light-high"),
    ("locate",         "Locate (horn+lights)","xva locate",               "mdi:map-marker-alert"),
]

# Selects. The engine override is three mutually exclusive modes rather than an on/off pair,
# so it is a select: a switch cannot hold "auto", and three buttons cannot show which one is
# active. The state metric reports forced-on/forced-off/auto, mapped to the option names.
# "auto" only releases an override, so the module does not ask for the PIN on it. The other 2
# get it appended, which is why the options carry their own command rather than sharing a stem.
SELECTS = [
    ("engine_mode", "Engine mode", "xva.v.e.mode", "xva engine",
     ["auto", "on", "off"],
     "{{ {'forced-on':'on','forced-off':'off'}.get(value, 'auto') }}",
     "mdi:engine"),
]

VALID_DC_UNITS = {
    "voltage": {"V", "mV"}, "current": {"A", "mA"}, "power": {"W", "kW"},
    "energy": {"Wh", "kWh"}, "temperature": {"°C", "°F"}, "battery": {"%"},
    "distance": {"km", "m", "mi"}, "speed": {"km/h", "mph"},
    "signal_strength": {"dBm"}, "duration": {"s", "min", "h"},
    "pressure": {"kPa", "bar", "psi"}, "energy_storage": {"kWh", "Wh"},
    "volume": {"L", "mL", "gal"},
}

# Units for metrics that may be EMPTY when the dump is taken. parse_value() infers type from
# the current value, so an event-driven metric that has not fired yet would be created as a
# plain text sensor -- and HA never records long-term statistics for those, so the data would
# not be graphable even once it starts flowing. Declaring them here types them regardless.
#
# state_class total_increasing (not measurement) for the drive-cycle counters: they climb and
# then reset to 0 when the car charges to 100%, which is exactly what total_increasing models.
# metric -> (unit, device_class, state_class)
# Metrics that never carry data on this car. Verified across a 9.5 km drive including 0.5 km
# on petrol, so the drivetrain, charging and climate paths were all exercised.
#
# Deliberately NOT excluded: fault indicators, where empty means healthy and removing the
# entity removes the only warning you would get. v.e.alarm, v.t.alert, v.t.health,
# v.b.12v.voltage.alert, v.b.c.voltage.alert, v.b.c.voltage.dev.max, v.e.aux12v all stay.
# v.e.handbrake also stays: it was simply never engaged during the test.
EXCLUDE = set()

# Second charge port / generator. Not fitted to a Volt at all.
EXCLUDE |= {f"v.g.{k}" for k in (
    "state","substate","mode","type","pilot","power","voltage","current","temp","climit",
    "efficiency","generating","kwh","kwh.grid","kwh.grid.total","limit.soc","limit.range",
    "timermode","timerstart","timestamp","duration.empty","duration.range","duration.soc")}

# Real OVMS features that no vehicle_voltampera code ever writes to.
EXCLUDE |= {
    "v.b.coulomb.recd","v.b.coulomb.recd.total","v.b.coulomb.used","v.b.coulomb.used.total",
    "v.b.p.level.avg","v.b.p.level.max","v.b.p.level.min","v.b.p.level.stddev",
    "v.c.12v.current","v.c.12v.power","v.c.12v.temp","v.c.12v.voltage",
    "v.e.cabinfan","v.e.cabinintake","v.e.cabinsetpoint","v.e.cabinvent",
    "v.i.power","v.i.efficiency", "v.e.serv.range","v.e.serv.time",
    "v.b.energy.recd","v.b.energy.recd.total","v.b.energy.used.total",
    "v.c.kwh.grid","v.c.kwh.grid.total",
    "m.serial",
    "v.b.health","v.b.range.full","v.b.range.speed",
    "v.c.efficiency","v.c.limit.range","v.c.timermode","v.c.timerstart",
    "v.e.cooling","v.e.heating","v.e.regenbrake","v.e.drivemode",
    "v.p.acceleration",
}

# NOT excluded even though empty here: these are empty because of how THIS module is set up,
# not because of the car, and someone else's setup will populate them. Excluding them would
# make this tool wrong for other people rather than merely verbose.
#   s.v2.connected, s.v2.peers          the v2 server, plenty of people still run it
#   v.e.c.config, v.e.c.login           server connection state, depends on which server
#   v.e.valet, v.p.valet.*              valet mode is a feature you can turn on
#   m.obdc2ecu.on, m.egpio.monitor      both are OVMS features you can enable

# Signals the car transmits as literal zeros. Proven from the raw frame: 0x0141 reads
# 00 00 00 10 00 00 00 00, and these live in bytes 4 to 7.
EXCLUDE |= {"xva.v.dc.distance", "xva.v.dc.econ", "xva.v.dc.econ.avg"}

# Never populated once, including through a drive with the engine running.
EXCLUDE |= {"xva.v.m.temp.trans"}

# Pinned at 100% permanently; a Volt has no serviceable fuel filter to report on.
EXCLUDE |= {"xva.v.e.fuelfilter.life"}

# Duplicate: read identical to xva.v.dc.energy.used on every sample, from a different message
# at different bit offsets. (v.b.energy.used is NOT a duplicate: that is OVMS's own per drive
# figure, while xva.v.dc.energy.used is the car's since the last full charge.)
EXCLUDE |= {"xva.v.dc.energy.st5"}

# Entities named to match BigThunderSR/onstar2mqtt, so dashboards and cards written for that
# addon work here after swapping the entity prefix. Their cards reference entity ids like
# sensor.<vehicle>_ev_battery_level, so the SUFFIX is what has to match; the prefix is set by
# ENTITY_PREFIX below.
#
# metric -> (suffix, friendly name, component, device_class, unit, state_class, icon, template)
ENTITY_PREFIX = "volt"


ONSTAR = {
  "v.b.soc":            ("ev_battery_level",        "sensor","battery","%","measurement",None,None),
  "v.b.range.est":      ("ev_range",                "sensor","distance","km","measurement",None,None),
  "xva.v.e.range.fuel": ("fuel_range",               "sensor","distance","km","measurement",None,None),
  "xva.v.range.total":  ("total_range",              "sensor","distance","km","measurement",None,None),
  "v.b.capacity":       ("ev_battery_capacity",     "sensor","energy_storage","kWh","measurement",None,None),
  "v.b.temp":           ("ev_battery_temperature",  "sensor","temperature","°C","measurement",None,None),
  "v.c.limit.soc":      ("ev_target_charge_level",  "sensor","battery","%","measurement",None,None),
  "v.c.state":          ("ev_charging_state",       "sensor",None,None,None,"mdi:ev-station",None),
  "v.c.temp":           ("ev_charging_temperature", "sensor","temperature","°C","measurement",None,None),
  "v.c.kwh":            ("ev_charging_energy",      "sensor","energy","kWh","total_increasing",None,None),
  "xva.v.c.energy.lifetime": ("ev_charging_lifetime_energy","sensor","energy","kWh","total_increasing",None,None),
  "v.p.odometer":       ("odometer",                "sensor","distance","km","total_increasing",None,None),
  "v.p.trip":           ("ev_trip_odometer",        "sensor","distance","km","measurement",None,None),
  "xva.v.b.chargecycle_econ": ("ev_trip_consumption","sensor",None,"kWh/100km","measurement",None,None),
  "xva.v.e.oil.life":   ("oil_life",                "sensor",None,"%","measurement","mdi:oil",None),
  "xva.v.e.fuel":       ("fuel_level",              "sensor",None,"%","measurement","mdi:gas-station",None),
  "v.e.temp":           ("ambient_air_temperature", "sensor","temperature","°C","measurement",None,None),
  "v.c.pilot":          ("ev_plug_state",           "binary_sensor","plug",None,None,None,None),
  "v.c.charging":       ("ev_charge_state",         "binary_sensor","battery_charging",None,None,None,None),
  "v.e.on":             ("ev_ignition",             "binary_sensor","running",None,None,None,None),
}

# The car reports all four tyres in one comma joined metric, while onstar2mqtt has an entity
# per corner. Split them with a value_template so the entity ids still line up.
TYRE_ORDER = ["left_front","right_front","left_rear","right_rear"]
for i, corner in enumerate(TYRE_ORDER):
    ONSTAR[f"__tyre_p_{i}"] = (f"tire_pressure_{corner}", "sensor", "pressure", "bar",
                               "measurement", None,
                               "{{ value.split(',')[%d] | float }}" % i)
    ONSTAR[f"__tyre_t_{i}"] = (f"tire_temperature_{corner}", "sensor", "temperature", "°C",
                               "measurement", None,
                               "{{ value.split(',')[%d] | float }}" % i)
TYRE_SRC = {"__tyre_p_": "v.t.pressure", "__tyre_t_": "v.t.temp"}

UNIT_HINTS = {
    "xva.v.dc.energy.used":    ("kWh", "energy",   "total_increasing"),
    "xva.v.dc.energy.st5":     ("kWh", "energy",   "total_increasing"),
    "xva.v.dc.energy.own":     ("kWh", "energy",   "total_increasing"),
    "xva.v.dc.distance":       ("km",  "distance", "total_increasing"),
    "xva.v.dc.distance.own":   ("km",  "distance", "total_increasing"),
    "xva.v.dc.dist.batt":      ("km",  "distance", "total_increasing"),
    "xva.v.dc.dist.fuel":      ("km",  "distance", "total_increasing"),
    "xva.v.dc.dist.total":     ("km",  "distance", "total_increasing"),
    "xva.v.dc.fuel.used":      ("L",   "volume",   "total_increasing"),
    "xva.v.dc.fuel.econ":      ("L/100km", None,   "measurement"),
    "xva.v.dc.batt.ratio":     ("%",   None,       "measurement"),
    "xva.v.dc.eff.batt":       ("%",   None,       "measurement"),
    "xva.v.dc.eff.cabin":      ("%",   None,       "measurement"),
    "xva.v.dc.eff.drive":      ("%",   None,       "measurement"),
    "xva.v.dc.eff.total":      ("%",   None,       "measurement"),
    "xva.v.dc.energy.pct1":    ("%",   None,       "measurement"),
    "xva.v.dc.energy.pct2":    ("%",   None,       "measurement"),
    "xva.v.dc.energy.pct3":    ("%",   None,       "measurement"),
    "xva.v.dc.energy.pct4":    ("%",   None,       "measurement"),
    # Economy: unit never confirmed against the dash, so no unit is asserted -- but it still
    # needs a state_class or HA will not record it.
    "xva.v.dc.econ":           (None,  None,       "measurement"),
    "xva.v.dc.econ.avg":       (None,  None,       "measurement"),
    "xva.v.c.energy.input":    ("kWh", "energy",   "total_increasing"),
    "xva.v.c.energy.lifetime": ("kWh", "energy",   "total_increasing"),
    "xva.v.c.inhibit":         (None,  None,       "measurement"),
    "xva.v.e.oil.life":        ("%",   None,       "measurement"),
    "xva.v.e.fuelfilter.life": ("%",   None,       "measurement"),
    "xva.v.b.soc.displayed":   ("%",   "battery",  "measurement"),
    "xva.v.b.soc.raw":         ("%",   "battery",  "measurement"),
    "xva.v.m.temp.trans":      ("°C",  "temperature", "measurement"),
    "xva.v.p.trip.ev":         ("km",  "distance", "total_increasing"),
    "xva.v.e.fuel.used":       ("L",   "volume",   "total_increasing"),
    "xva.v.b.chargecycle_econ":("kWh/100km", None, "measurement"),
    # Charge history is raw hex of an unvalidated layout: text on purpose, no unit.
    "xva.v.c.hist1": (None, None, None), "xva.v.c.hist2": (None, None, None),
    "xva.v.c.hist3": (None, None, None), "xva.v.c.hist4": (None, None, None),
    "xva.v.c.hist5": (None, None, None),
}

UNIT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)\s*(.*)$")
UNIT_MAP = {
    "Sec": ("s", "duration"), "sec": ("s", "duration"),
    "V": ("V", "voltage"), "A": ("A", "current"),
    "kW": ("kW", "power"), "W": ("W", "power"),
    "kWh": ("kWh", "energy"), "Wh": ("Wh", "energy"),
    "°C": ("°C", "temperature"), "C": ("°C", "temperature"),
    "%": ("%", None), "km": ("km", "distance"), "Km": ("km", "distance"),
    "kph": ("km/h", "speed"), "km/h": ("km/h", "speed"),
    "Ah": ("Ah", None), "rpm": ("rpm", None), "dBm": ("dBm", "signal_strength"),
    "kPa": ("kPa", "pressure"),
}


def metric_for_onstar(key):
    """ONSTAR keys are metric names, except the synthetic per corner tyre entries."""
    for pfx, src in TYRE_SRC.items():
        if key.startswith(pfx):
            return src
    return key


def topic_for(metric):
    return PREFIX + "metric/" + metric.replace(".", "/")


def object_id(metric):
    return re.sub(r"[^a-z0-9_]", "_", metric.lower())


def base(metric, name):
    return {
        "name": name,
        "unique_id": f"{NODE}_{object_id(metric)}",
        "object_id": f"{NODE}_{object_id(metric)}",
        "state_topic": topic_for(metric),
        "device": DEVICE,
        "availability": AVAIL,
    }


def parse_value(raw):
    """Return (kind, unit, device_class). kind in {bool, number, text}."""
    v = raw.strip()
    if v in ("yes", "no"):
        return "bool", None, None
    m = UNIT_RE.match(v)
    if m and "," not in v:
        unit_raw = m.group(2).strip()
        if unit_raw == "":
            return "number", None, None
        if unit_raw in UNIT_MAP:
            u, dc = UNIT_MAP[unit_raw]
            return "number", u, dc
        return "text", None, None          # unrecognised suffix, keep as text
    return "text", None, None


def emit(out, comp, metric, cfg):
    t = f"homeassistant/{comp}/{NODE}/{object_id(metric)}/config"
    out.append(f"{t}\t{json.dumps(cfg, ensure_ascii=False)}")


def main():
    # Authoritative types from the OVMS source, if it is available. Without this, typing falls
    # back to guessing from the current value, and around half of all metrics are empty on any
    # given dump (event driven, or only populated while the car is awake). An empty value looks
    # like text, which silently turns booleans into text sensors and numbers into untyped ones
    # that Home Assistant will not record statistics for.
    # The PIN is an argument, never a default and never committed: this repo is public.
    pin = os.environ.get("OVMS_PIN", "")
    argv = []
    for a in sys.argv[1:]:
        if a.startswith("--pin="):
            pin = a.split("=", 1)[1]
        else:
            argv.append(a)
    sys.argv = [sys.argv[0]] + argv

    types = {}
    src = os.environ.get("OVMS_SRC")
    if len(sys.argv) > 2:
        src = sys.argv[2]
    if src and os.path.isdir(src):
        types = build_types(src)
        sys.stderr.write(f"typed {len(types)} metrics from {src}\n")
    else:
        sys.stderr.write("no OVMS source given, falling back to value-based typing\n")

    metrics = {}
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if not line.strip():
            continue
        parts = re.split(r"\s{2,}", line.strip(), maxsplit=1)
        name = parts[0].strip()
        val = parts[1].strip() if len(parts) > 1 else ""
        if not re.match(r"^[a-z0-9_.]+$", name):
            continue
        metrics[name] = val

    out = []
    n_cur = n_diag = 0

    n_skip = 0
    # onstar2mqtt-named entities first, so a card written for that addon works here.
    n_onstar = 0
    for key, (suffix, comp, dc, unit, sc, icon, tmpl) in sorted(ONSTAR.items()):
        src = metric_for_onstar(key)
        if src not in metrics:
            continue
        cfg = {
            "name": suffix.replace("_", " ").capitalize(),
            "unique_id": f"{ENTITY_PREFIX}_{suffix}",
            "object_id": f"{ENTITY_PREFIX}_{suffix}",
            "state_topic": topic_for(src),
            "device": DEVICE,
            "availability": AVAIL,
        }
        if tmpl:
            cfg["value_template"] = tmpl
        if comp == "binary_sensor":
            cfg.update({"payload_on": "yes", "payload_off": "no"})
            if dc: cfg["device_class"] = dc
        else:
            if dc and unit and unit in VALID_DC_UNITS.get(dc, set()):
                cfg["device_class"] = dc
            if unit: cfg["unit_of_measurement"] = unit
            if sc: cfg["state_class"] = sc
        if icon: cfg["icon"] = icon
        if comp == "sensor" and src in PRECISION:
            cfg["suggested_display_precision"] = PRECISION[src]
        t = f"homeassistant/{comp}/{NODE}/{ENTITY_PREFIX}_{suffix}/config"
        out.append(f"{t}\t{json.dumps(cfg, ensure_ascii=False)}")
        n_onstar += 1

    # A metric already published under its onstar2mqtt name must not also appear under its old
    # curated name, or every value would exist twice in Home Assistant.
    onstar_sources = {metric_for_onstar(k) for k in ONSTAR}

    for metric, raw in sorted(metrics.items()):
        if metric in EXCLUDE:
            n_skip += 1
            continue
        if metric in onstar_sources:
            continue
        if metric in CURATED:
            label, dc, unit, sc, icon = CURATED[metric]
            cfg = base(metric, label)
            if dc and unit and unit in VALID_DC_UNITS.get(dc, set()):
                cfg["device_class"] = dc
            if unit:
                cfg["unit_of_measurement"] = unit
            if sc:
                cfg["state_class"] = sc
            if icon:
                cfg["icon"] = icon
            if metric in PRECISION:
                cfg["suggested_display_precision"] = PRECISION[metric]
            emit(out, "sensor", metric, cfg)
            n_cur += 1
            continue

        if metric in CURATED_BINARY:
            label, dc, icon, invert = CURATED_BINARY[metric]
            cfg = base(metric, label)
            if invert:
                cfg.update({"payload_on": "no", "payload_off": "yes"})
            else:
                cfg.update({"payload_on": "yes", "payload_off": "no"})
            if dc:
                cfg["device_class"] = dc
            if icon:
                cfg["icon"] = icon
            emit(out, "binary_sensor", metric, cfg)
            n_cur += 1
            continue

        # Uncurated: still exposed, but tagged diagnostic and given a readable name rather
        # than the raw dotted metric path.
        kind, unit, dc = parse_value(raw)
        # Declared type beats an inferred one. Only the value's own unit survives if the
        # source says Other, since the running metric may carry a unit the declaration omits.
        if metric in types:
            tkind, tunit = types[metric]
            kind = tkind
            hu, hdc = OVMS_UNIT_MAP.get(tunit, (None, None))
            if hu:
                unit, dc = hu, hdc
        cfg = base(metric, friendly(metric))
        cfg["entity_category"] = "diagnostic"
        if metric in PRECISION:
            cfg["suggested_display_precision"] = PRECISION[metric]
        if kind == "bool":
            cfg.update({"payload_on": "yes", "payload_off": "no"})
            emit(out, "binary_sensor", metric, cfg)
        elif metric in UNIT_HINTS:
            # Declared type wins over whatever the current (possibly empty) value looks like.
            hu, hdc, hsc = UNIT_HINTS[metric]
            if hdc and hu and hu in VALID_DC_UNITS.get(hdc, set()):
                cfg["device_class"] = hdc
            if hu:
                cfg["unit_of_measurement"] = hu
            if hsc:
                cfg["state_class"] = hsc
            emit(out, "sensor", metric, cfg)
        else:
            if kind == "number":
                if dc and unit and unit in VALID_DC_UNITS.get(dc, set()):
                    cfg["device_class"] = dc
                if unit:
                    cfg["unit_of_measurement"] = unit
                    cfg["state_class"] = "measurement"
            emit(out, "sensor", metric, cfg)
        n_diag += 1

    for key, label, metric, cmd, options, tmpl, icon in SELECTS:
        cfg = {
            "name": label,
            "unique_id": f"{ENTITY_PREFIX}_{key}",
            "object_id": f"{ENTITY_PREFIX}_{key}",
            "state_topic": topic_for(metric),
            "value_template": tmpl,
            "options": options,
            # HA sends the chosen option as the payload; OVMS needs "xva engine <option>", so
            # the command template prepends the verb. "auto" only releases an override and the
            # module does not ask for the PIN on it, so the template appends it conditionally
            # rather than always.
            "command_topic": f"{PREFIX}client/{CMD_CLIENT}/command/{key}",
            "command_template": cmd + " {{ value }}{{ ' {PIN}' if value != 'auto' else '' }}",
            "device": DEVICE, "availability": AVAIL, "icon": icon,
        }
        t = f"homeassistant/select/{NODE}/{ENTITY_PREFIX}_{key}/config"
        out.append(f"{t}\t{json.dumps(cfg, ensure_ascii=False)}")

    for entry in SWITCHES:
        key, label, metric, pay_on, pay_off, icon = entry[:6]
        extra = entry[6] if len(entry) > 6 else {}
        cfg = {
            "name": label,
            "unique_id": f"{ENTITY_PREFIX}_{key}",
            "object_id": f"{ENTITY_PREFIX}_{key}",
            "state_topic": topic_for(metric),
            "state_on": "yes", "state_off": "no",
            "command_topic": f"{PREFIX}client/{CMD_CLIENT}/command/{key}",
            "payload_on": pay_on, "payload_off": pay_off,
            "optimistic": False,
            "device": DEVICE, "availability": AVAIL, "icon": icon,
        }
        cfg.update(extra)
        t = f"homeassistant/switch/{NODE}/{ENTITY_PREFIX}_{key}/config"
        out.append(f"{t}\t{json.dumps(cfg, ensure_ascii=False)}")

    for key, label, metric, pay_lock, pay_unlock, icon in LOCKS:
        cfg = {
            "name": label,
            "unique_id": f"{ENTITY_PREFIX}_{key}",
            "object_id": f"{ENTITY_PREFIX}_{key}",
            "state_topic": topic_for(metric),
            "state_locked": "yes",
            "state_unlocked": "no",
            "command_topic": f"{PREFIX}client/{CMD_CLIENT}/command/{key}",
            "payload_lock": pay_lock,
            "payload_unlock": pay_unlock,
            "device": DEVICE,
            "availability": AVAIL,
            "icon": icon,
        }
        t = f"homeassistant/lock/{NODE}/{ENTITY_PREFIX}_{key}/config"
        out.append(f"{t}\t{json.dumps(cfg, ensure_ascii=False)}")

    for entry in BUTTONS:
        key, label, command, icon = entry[:4]
        extra = entry[4] if len(entry) > 4 else {}
        cfg = {
            "name": label,
            "unique_id": f"{NODE}_btn_{key}",
            "object_id": f"{NODE}_btn_{key}",
            "command_topic": f"{PREFIX}client/{CMD_CLIENT}/command/{key}",
            "payload_press": command,
            "device": DEVICE,
            "availability": AVAIL,
            "icon": icon,
        }
        cfg.update(extra)
        t = f"homeassistant/button/{NODE}/btn_{key}/config"
        out.append(f"{t}\t{json.dumps(cfg, ensure_ascii=False)}")

    body = "\n".join(out)
    if "{PIN}" in body:
        if not pin:
            sys.stderr.write(
                "error: some commands need the vehicle PIN and none was given.\n"
                "       pass --pin=1234 or set OVMS_PIN. Without it Home Assistant would\n"
                "       publish buttons the car refuses.\n")
            sys.exit(1)
        body = body.replace("{PIN}", pin)

    sys.stderr.write(f"onstar={n_onstar} curated={n_cur} diagnostic={n_diag} "
                 f"buttons={len(BUTTONS)} excluded={n_skip} total={len(out)}\n")
    print(body)


if __name__ == "__main__":
    main()
