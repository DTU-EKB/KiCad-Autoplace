# Single/double-sided routing — design

**Date:** 2026-06-30
**Status:** Approved

## Problem

The router always routes on both copper layers (F.Cu + B.Cu). For single-sided
etch/CNC boards the user needs to force routing onto the **bottom** layer only,
leaving nets that can't route on one layer as ratsnest to hand-jumper.

## Decisions (from brainstorming)

- A **Routing** control with two modes: **Double-sided** (default, current
  behaviour) and **Single-sided (bottom)** = B.Cu only.
- Single-sided leaves uncrossable nets **unrouted** (no two-stage top fallback).
- Single-sided routes on **B.Cu** (the etch/CNC side). `SetCopperLayerCount(1)`
  keeps F.Cu (wrong side), so the mechanism constrains the DSN instead.
- Independent of the fabrication profile (mixable); affects the routing step only
  (placement unchanged).

## Mechanism

In `route_once`, gated by a new `sides` parameter (default `2`):

- `sides == 2`: unchanged.
- `sides == 1`: after `ExportSpecctraDSN`, rewrite the DSN so the only routable
  copper `signal` layer is B.Cu — every other `.Cu` layer's `(type signal)`
  becomes `(type power)` (non-routable in FreeRouting). FreeRouting then routes
  what it can on B.Cu and leaves the rest unrouted.
  - Verification during implementation: a real route must yield tracks on B.Cu
    only. If `power` proves unsuitable, the fallback is the same transform
    removing the non-B.Cu layers from the routable set — identical user-facing
    result. (The pure transform is swappable without touching callers.)

## GND-zone interaction

`force_gnd_zones` becomes sides-aware (`force_gnd_zones(pcb, sides=2)`):

- `sides == 2`: today's behaviour — force + fill B.Cu and F.Cu GND pours.
- `sides == 1`: **remove** F.Cu copper zones (single-sided has no top copper),
  force the B.Cu zone to GND, and fill. So the single-sided output has a bottom
  GND plane and no top copper.

`apply_placement` keeps calling `force_gnd_zones(pcb)` with the default
`sides=2` — the single-sided decision is a routing-time choice applied in
`route_once`/`refine`, not at placement.

## Architecture

### New pure module `autoplace/dsn.py` (no pcbnew)

```python
def single_sided_dsn(text: str, keep: str = "B.Cu") -> str:
    """Make every copper signal layer except `keep` non-routable.

    Turns `(layer <name> (type signal))` into `(type power)` for every layer
    whose name ends in '.Cu' and != keep. Leaves `keep` and all non-copper
    layers untouched. Pure string transform -> unit-testable.
    """
```

### `routing.route_once(pcb_path, jar, passes, stem=None, sides=2)`

- after `LoadBoard`: `force_gnd_zones(board, sides=sides)`.
- after `ExportSpecctraDSN`, if `sides == 1`: read the DSN, apply
  `dsn.single_sided_dsn`, write it back, before invoking FreeRouting.
- after `ImportSpecctraSES`: `force_gnd_zones(board, sides=sides)` (refill).

### `kicad_io.force_gnd_zones(pcb, sides=2)`

- Collect zones via `[pcb.GetArea(i) for i in range(pcb.GetAreaCount())]`.
- `sides == 1`: `pcb.Remove(z)` for zones on F.Cu (and not B.Cu); set remaining
  B.Cu/F.Cu zones to the GND net.
- `sides == 2`: set all B.Cu/F.Cu zones to GND (current).
- Fill `pcb.Zones()` if anything changed. No-op when there is no GND net.
- Returns `{"set": [...], "removed": [...]}`.

### `refine.refine(..., sides=2)`

- Add `sides` param; pass `sides=sides` into the `routing.route_once(...)` call
  in `route_eval`.

### `cli.py cmd_refine`

- Read `sides = int(os.environ.get("SIDES", "2"))`; pass `sides=sides` to
  `refine_mod.refine(...)`. (`place`/`place-multi` unaffected — they don't
  route; their `force_gnd_zones` keeps the `sides=2` default.)

### App

- `index.html`: a **Routing** `<select id="sides">` in `.controls`:
  `2` → "Double-sided", `1` → "Single-sided (bottom)". Default `2`.
- `renderer.js`: include `sides: parseInt($("sides").value, 10)` in the
  `runRefine` options.
- `main.js`: in `runRefine`, set `env.SIDES = String(sides || 2)`.
- `preload.js`: no change (passthrough).

## Files

- Create: `plugin/plugins/autoplace/dsn.py`, `tests/test_dsn.py`
- Modify: `plugin/plugins/autoplace/routing.py`,
  `plugin/plugins/autoplace/kicad_io.py` (force_gnd_zones sides param),
  `plugin/plugins/autoplace/refine.py`, `cli.py`,
  `app/main.js`, `app/renderer/index.html`, `app/renderer/renderer.js`.

## Data flow

```
Routing = Single-sided  →  Refine  →  SIDES=1
  → cli refine → refine(sides=1) → route_once(sides=1)
     → force_gnd_zones(sides=1): drop F.Cu zone, B.Cu pour = GND
     → ExportSpecctraDSN → single_sided_dsn (F.Cu signal→power)
     → FreeRouting routes B.Cu only; uncrossable nets left unrouted
```

## Error handling

- Unknown/missing `SIDES` → default 2.
- A board with no F.Cu zone in single-sided mode → nothing to remove; B.Cu pour
  still forced/filled.
- `single_sided_dsn` on a DSN with only B.Cu → unchanged (no other signal Cu).

## Testing

Pure-Python `tests/test_dsn.py` (system python):
1. `single_sided_dsn` turns `(layer F.Cu (type signal))` into `(type power)` and
   leaves `(layer B.Cu (type signal))` untouched.
2. Inner copper layers (`In1.Cu` signal) are also made `power`; a non-copper
   layer line is untouched.
3. Idempotent: applying twice equals applying once.
4. `keep` is configurable (e.g. `keep="F.Cu"` leaves F.Cu signal, makes B.Cu
   power).

End-to-end (KiCad python, manual, as with the GND fix): single-sided route yields
tracks on B.Cu only and no F.Cu copper zone; double-sided unchanged.

## Out of scope (YAGNI)

- Two-stage (bottom-then-top) single-sided routing.
- Choosing an arbitrary single layer via the UI (bottom only).
- Reducing the output board's copper-layer count to 1 (F.Cu left empty is fine;
  the user can set layer count in KiCad).
- Applying `sides` to `place` output (routing-time decision only).
