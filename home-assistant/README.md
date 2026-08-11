# Chevrolet Volt in Home Assistant, over MQTT

Everything needed to turn the car into a proper Home Assistant device with 235 entities and 12 working control buttons.

OVMS publishes every metric to MQTT but emits no Home Assistant discovery, so out of the box nothing appears in HA.
The data is on the broker and HA ignores it.

![The dashboard](dashboard.png)

| File | What it is |
|---|---|
| `mqtt-mapping.md` | every OVMS metric and the entity it becomes: units, device classes, display precision, diagnostic flag, and the command behind each button |
| `ultra-card.yaml` | a ready-to-paste [Ultra Card](https://github.com/WJDDesigns/Ultra-Card) dashboard: photo, battery bar with the charge-limit marker, fuel bar, and one control per function rather than a pair |
| `history-card.yaml` | a plain entities card to sit underneath it, every row opening more-info and its history |
| `../tools/gen_discovery.py` | generates the discovery configs from the module's own `metrics list` |
| `../tools/names.py` | turns `xva.v.ac.evap_temp` into "A/C evaporator temperature" |
| `../tools/ws_save.py` | writes a Lovelace dashboard over HA's websocket API, no restart |

## Topics

```
ovms/<user>/<vehicleid>/…            default prefix
ovms/volt/metric/v/b/soc             a metric: dots become slashes
ovms/volt/client/<id>/command/<n>    send an OVMS shell command
ovms/volt/client/<id>/response/<n>   its output comes back here
ovms/volt/metric/s/v3/connected      last-will topic; use for availability
```

:warning: The command topic runs an authorized shell, so anything you can type into OVMS can be sent from HA!

That is what the buttons use:

```yaml
payload_press: "climatecontrol on"
command_topic: "ovms/volt/client/hass/command/climate_on"
```

## How to generate the discovery configs

```bash
# on the module
metrics list > metrics-all.txt

# locally. The second argument points at the OVMS source so entity types come from the
# metric declarations rather than being guessed from whatever value each one happens to
# hold: about half are empty on any given dump, and an empty value reads as text, which
# turns booleans into text sensors and leaves numbers without a state class so Home
# Assistant never records statistics for them.
python3 tools/gen_discovery.py metrics-all.txt /path/to/OVMS.V3/main > discovery.tsv

# publish, retained so HA rebuilds them on restart
while IFS=$'\t' read -r topic payload; do
  mosquitto_pub -h <broker> -p 1883 -u ovms -P "$PW" -t "$topic" -m "$payload" -r -q 1
done < discovery.tsv
```

:warning: Publishing an empty retained payload to a config topic deletes that entity!

## Things that cost me time

### Curated vs diagnostic

32 metrics get real names, units and device classes.
The remaining 191 are published with `entity_category: diagnostic`, which keeps them out of dashboards and auto-generated UI while remaining queryable.
Without that split the device page is unusable.

### `entity_category` never appears in `/api/states`

It lives in the entity registry, so verifying it through the REST API always looks like it failed.
Read `.storage/core.entity_registry` instead.

### HA inverts some binary device classes

For `lock`, `on` means *unlocked*.
OVMS publishes `v.e.locked = yes` when locked, so the payloads must be swapped or the state reads backwards.
The same trap applies to `problem`, `safety` and `battery`.

### Set `suggested_display_precision`

Otherwise a whole-number range shows as `47.00 km`.

### `expire_after` will not detect a stale metric here

OVMS re-sends every metric every 20 minutes regardless of change (`server.v3` sendall), so a frozen value keeps being re-delivered and looks fresh.
Values that only exist while the car is awake have to be zeroed by the firmware on sleep instead.

### Entity IDs are permanent

HA derives them from the device and first-seen name and never renames them, so `sensor.chevrolet_volt_xva_v_ac_evap_temp` keeps its ugly ID even after the friendly name is fixed.
Only the display name changes.
That is a feature, nothing you reference later breaks.

## Ultra Card notes

- Module types are singular: `bar` and `icon`, not `bars`/`icons`. An unknown type blanks the whole card.
- `image_type: url` is mandatory. The default renders a built-in stock photo instead of yours, which looks like it worked.
- The charge-limit marker on the battery bar is two keys: `limit_entity` + `limit_color`.
- `icon_mode: static` icons never render a label. That is hardcoded, not configurable, so every labelled button must be `icon_mode: entity` with a backing entity, which also drives its highlight color.
- Entries in `icons[]` get no default merging, unlike module-level keys.
- A mistyped bar entity renders a plausible demo 65% bar rather than an error.
- Conditional visibility lives on rows, columns and modules (`display_mode` plus `display_conditions`). Individual entries in `icons[]` cannot be hidden: `IconConfig` has `tap_action` but no `display_conditions`. To show one of two icons, give each its own icon module and put both modules in the same column.
- `display_mode` is `always`, `every`, `any` or `never`. Use `every` for a single condition and `any` for a value that could be one of several, such as the two window transitions.
- `tap_action: perform-action` accepts the legacy `service` and `entity` keys, and injects `entity_id` from `entity` only when no explicit `data` is given.
- `action: toggle` calls `homeassistant.toggle`, which forwards to `<domain>.toggle` and does nothing when that service does not exist. The `lock` domain only has `lock`, `unlock` and `open`, so the doors need explicit `lock.lock` and `lock.unlock` calls rather than a toggle.
- Bars take `tap_action` plus separate `left_tap_action` and `right_tap_action`, so the bar and each of its two labels can open more-info for a different entity.

## Controls that carry state

Buttons cannot show state, so anything with an on/off pair is published as a domain that can:

| entity | domain | state source |
|---|---|---|
| Doors | `lock` | `v.e.locked` |
| Climate | `switch` | `v.e.hvac` |
| Charging | `switch` | `v.c.charging` |
| Charge to 100% once | `switch` | `xva.v.c.limit.override` |
| Windows | `switch` | `xva.v.e.windows` |

Horn, flash, locate and wake stay buttons, because they are momentary actions with nothing to usefully toggle.

Trunk is still a button but ships `enabled_by_default: false`, so it is registered and disabled rather than sitting on a dashboard waiting to be fat fingered.

:warning: Releasing the hatch remotely cannot be undone remotely, the car stays unlatched until somebody walks over and shuts it!

Enable it by hand if you want it.

### Windows

Windows are a `switch`, on being down.
The metric carries `opening` and `closing` as well as the two resting states, so the discovery config maps each transitional value onto the position it is heading for:

```
value_template: "{{ 'open' if value in ['open', 'opening'] else 'closed' }}"
state_on: open
state_off: closed
```

The toggle then flips the moment the glass starts moving instead of after it settles.
A payload matching neither state is ignored outright rather than clearing the state: `switch.py` only assigns when the templated payload is a key of its state map.

### Why not a cover for the windows?

I tried a `cover` first and gave up on it.
The car does report real per window position, so it decodes fine and I kept the sensors, but Home Assistant's cover control is a poor fit.
Its open button is always an up arrow wired unconditionally to `cover.open_cover`, and opening a car window moves the glass down, so the arrow always points the wrong way.
Nothing in the config swaps it.

Inverting `position_open` / `position_closed` does work arithmetically but is a trap: the frontend derives `canOpen` from `current_position === 100`, so an inverted cover disables its own open button whenever the windows are shut, and reports a shut window as Open.
Two plain buttons are clearer than any of that.
