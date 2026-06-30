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
- Single-sided copper ends on **B.Cu** (the etch/CNC side). FreeRouting routes the
  one layer KiCad exposes (F.Cu); the result is flipped to B.Cu afterward.
- Independent of the fabrication profile (mixable); affects the routing step only
  (placement unchanged).

## Mechanism (as built)

The original plan rewrote the DSN to mark non-bottom layers non-routable. That
was abandoned after verification: **FreeRouting ignores the Specctra layer
`type`** (a `power` F.Cu still got ~500 tracks). The reliable lever is KiCad's
copper-layer count, with two prerequisites learned from real routes.

In `route_once`, gated by a new `sides` parameter (default `2`):

- `sides == 2`: unchanged.
- `sides == 1` (single-sided, clean slate, on **F.Cu**):
  1. **Strip existing routing** from `pcb_path` textually (drop every top-level
     `(segment …)` / `(via …)` / `(arc …)`). Done on the *file* before
     `LoadBoard` because in-process `pcbnew` track removal access-violates. This
     also prevents leftover B.Cu wires from referencing a layer we are about to
     remove (which makes FreeRouting reject the DSN).
  2. `board.SetCopperLayerCount(1)` → one copper layer (KiCad keeps **F.Cu**).
  3. **Move any B.Cu pour onto F.Cu** (`zone.SetLayer(F.Cu)`) — an exported zone
     referencing the now-absent B.Cu breaks the DSN
     (`layer name 'B.Cu' not found`). `SetLayer` avoids `pcb.Remove`, which
     corrupts the KiCad-10 connectivity object.
  4. Export + route as usual; uncrossable nets are left unrouted.
  5. **Flip the routed copper to B.Cu** (`_flip_to_bottom`): reload the saved
     board (so `GetTracks` is iterable again — it is not on the just-imported
     board), re-enable B.Cu, and move every F.Cu track and pour to B.Cu.
     Footprint pads are untouched, so components stay on top. The board keeps two
     layers with F.Cu empty — fine for a single-sided etch.

FreeRouting routes the front layer KiCad exposes; the result is flipped so the
copper lands on the **bottom** (etch side). Verified end-to-end: 0 warnings,
tracks and pours all on B.Cu.

## GND-zone interaction

`force_gnd_zones(pcb)` is unchanged from the GND-enforcement work, with one
addition: it **skips zones on a disabled copper layer** (so it does not try to
fill a B.Cu zone after `SetCopperLayerCount(1)`). The single-sided layer
reduction and the B.Cu→F.Cu zone move live in `route_once`, not in
`force_gnd_zones`. `apply_placement` is unchanged (placement-time, double-sided).

## Architecture

### New pure module `autoplace/strip.py` (no pcbnew, no sexpdata)

```python
def strip_tracks(text: str, kinds=("segment", "via", "arc")) -> tuple[str, int]:
    """Remove top-level routing s-expressions from a .kicad_pcb text.

    Balanced-paren, quote-aware. Footprints / pads / zones / nets survive.
    Returns (stripped_text, removed_count). Pure -> unit-testable.
    """
```

### `routing.route_once(pcb_path, jar, passes, stem=None, sides=2)`

- if `sides == 1`: read `pcb_path`, `strip_tracks`, write back (clean slate).
- `LoadBoard`.
- if `sides == 1`: `SetCopperLayerCount(1)` and move B.Cu zones to F.Cu.
- `force_gnd_zones(board)` (set GND + fill enabled-layer pours).
- export → FreeRouting → import → `force_gnd_zones(board)` → save.

### `kicad_io.force_gnd_zones(pcb)`

- Set every B.Cu/F.Cu copper zone **on an enabled layer** to the GND net, fill.
- No `sides` param; skips zones whose layer is disabled (e.g. B.Cu after the
  single-sided layer reduction). Returns `{"set": [...]}`.

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

- Create: `plugin/plugins/autoplace/strip.py`, `tests/test_strip.py`
- Modify: `plugin/plugins/autoplace/routing.py`,
  `plugin/plugins/autoplace/kicad_io.py` (force_gnd_zones skips disabled layers),
  `plugin/plugins/autoplace/refine.py`, `cli.py`,
  `app/main.js`, `app/renderer/index.html`, `app/renderer/renderer.js`.

## Data flow

```
Routing = Single-sided  →  Refine  →  SIDES=1
  → cli refine → refine(sides=1) → route_once(sides=1)
     → strip_tracks(pcb_path)              clean slate
     → SetCopperLayerCount(1); B.Cu pour → F.Cu
     → force_gnd_zones; ExportSpecctraDSN → FreeRouting (one layer, F.Cu)
     → uncrossable nets left unrouted
```

## Error handling

- Unknown/missing `SIDES` → default 2.
- A board with no F.Cu zone in single-sided mode → nothing to remove; B.Cu pour
  still forced/filled.
- `single_sided_dsn` on a DSN with only B.Cu → unchanged (no other signal Cu).

## Testing

Pure-Python `tests/test_strip.py` (system python):
1. `strip_tracks` removes `(segment …)` / `(via …)` / `(arc …)` and keeps
   footprints, pads, and zones; count is correct.
2. The `(net N "…")` declaration survives (it is not a track).
3. Parens inside quoted strings don't confuse the matcher.
4. No tracks → no-op (text unchanged).

End-to-end (KiCad python, manual): single-sided route on the system board yields
0 `B.Cu not found` warnings, tracks on F.Cu only, the B.Cu pour relocated to
F.Cu, and a higher routed-% than the no-strip attempt; double-sided unchanged.

## Out of scope (YAGNI)

- Two-stage (bottom-then-top) single-sided routing.
- Choosing which physical side via the UI (single-sided always lands on B.Cu).
- Applying `sides` to `place` output (routing-time decision only).
