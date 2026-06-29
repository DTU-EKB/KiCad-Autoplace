# Fabrication profile selector — design

**Date:** 2026-06-30
**Status:** Approved

## Problem

The board's copper clearance and track width depend on how the board is
manufactured. Two pipelines are in use:

- **Fiber laser** (xTool): clearance 0.8 mm, track 1.0 mm (laser-process
  non-negotiables).
- **CNC mill** (0.8 mm endmill): clearance 0.85 mm, track 1.0 mm — copper gaps
  must be ≥ the endmill diameter to be millable.

Today the placement margin is hard-wired to 0.8 mm and the clearance/track rules
come from whatever `.kicad_pro` the user already had. The user wants to pick the
fabrication method in the app; that choice should set the right clearance/track
everywhere it matters.

## Profiles

```
laser : clearance 0.80, track 1.0
cnc   : clearance 0.85, track 1.0
```

## What a profile drives (outputs only — the input board is never modified)

1. **Placement margin** — `engine.place(margin=…)` uses the profile's clearance
   (was hard-wired 0.8), so parts spread far enough that the copper between them
   is etchable/millable. The laser-vs-CNC choice therefore subtly changes the
   layout, not only the rules.
2. **Net-class** in the output `.kicad_pro`: `net_settings.classes[*].clearance`
   and `track_width` — read by FreeRouting (via DSN export).
3. **DRC rules** in the output `.kicad_pro`:
   `board.design_settings.rules.min_clearance` and `min_track_width` — read by
   DRC, so routing and DRC agree with the fab.

## Architecture

### New module `autoplace/fabrication.py` (pure, no pcbnew)

```python
PROFILES = {
    "laser": {"clearance": 0.8,  "track": 1.0},
    "cnc":   {"clearance": 0.85, "track": 1.0},
}

def margin_for(fab: str) -> float
    # PROFILES[fab]["clearance"]; raises KeyError-style ValueError on unknown fab

def apply_to_project(pro_path: str, fab: str) -> bool
    # Load the .kicad_pro JSON, set on every net class:
    #   clearance, track_width
    # and in board.design_settings.rules:
    #   min_clearance, min_track_width
    # Write back (indent=2). Returns True if written, False if pro_path missing.
    # Leaves all other keys untouched.
```

Unknown fab names raise `ValueError` (callers pass a validated dropdown value;
this guards typos). `apply_to_project` is a no-op returning `False` when the
project file does not exist (placement still works; only the rules can't be set).

### Wiring

- **`cli.py`** reads `FAB` env var (default `"cnc"`, mirroring the current system
  board). A helper `_fab()` returns the validated profile name.
  - `cmd_place`: `engine.place(..., margin=margin_for(fab))`; after
    `copy_project`, call `fabrication.apply_to_project(out_pro, fab)` where
    `out_pro = splitext(out_path)[0] + ".kicad_pro"`.
  - `cmd_refine`: same — pass `place_margin=margin_for(fab)` into
    `refine_mod.refine(...)` and apply to the output project after
    `copy_project`.
  - `cmd_place_multi`: pass `margin=margin_for(fab)` into
    `multiseed.run_candidates(...)`. No project write (preview only).
- **`multiseed.run_candidates`** gains a `margin: float = 0.8` param, threaded
  into each `engine.place(..., margin=margin)` call, so previews match the
  committed board (commit re-runs `place` with the same `FAB` → same margin →
  identical deterministic layout).
- **`refine_mod.refine`** gains a new `place_margin: float = 0.8` param, used in
  its internal `anneal_mod.anneal(cand, ..., margin=place_margin)` call (which
  today hard-wires `margin=0.8`). This is distinct from the existing
  `margin_conns`/`margin_pct` (a routing connection-count improvement threshold)
  — do not overload those.
- **App:**
  - `index.html`: a "Fabrication" `<select id="fab">` in `.controls` with
    `laser` / `cnc` options (default `cnc`).
  - `renderer.js`: include `fab: $("fab").value` in the `runPlace`,
    `runPlaceMulti`, and `runRefine` option objects.
  - `preload.js`: no change (opts are passthrough).
  - `main.js`: in `runPlace`, `runPlaceMulti`, `runRefine`, set
    `env.FAB = fab || "cnc"`.

## Data flow

```
Fab dropdown = "laser"
  → run → FAB=laser → engine.place(margin=0.8) + output .kicad_pro
          (netclass clearance 0.8/track 1.0, DRC min_clearance 0.8/min_track 1.0)
Fab dropdown = "cnc"
  → run → FAB=cnc   → engine.place(margin=0.85) + output .kicad_pro
          (netclass clearance 0.85/track 1.0, DRC min_clearance 0.85/min_track 1.0)
```

## Error handling

- Unknown `FAB` value → `ValueError` in `_fab()`; `cli.py` surfaces it on stderr
  and exits non-zero (the app only ever sends valid values).
- Missing output `.kicad_pro` → `apply_to_project` returns `False`; placement
  output is still written, and the existing "net-class rules carried over" note
  reflects whether the project was present.

## Testing

Pure-Python `tests/test_fabrication.py` (system python, no pcbnew):
1. `margin_for("laser") == 0.8`, `margin_for("cnc") == 0.85`.
2. `margin_for("bogus")` raises `ValueError`.
3. `apply_to_project` on a temp `.kicad_pro` JSON sets, for each profile, all of:
   every net class `clearance` + `track_width`, and
   `design_settings.rules.min_clearance` + `min_track_width`; an unrelated key
   (e.g. `rules.min_hole_to_hole`) is left unchanged.
4. `apply_to_project` on a non-existent path returns `False` and raises nothing.

App wiring (dropdown → env → applied rules) is verified manually against the
system board: run with each profile and confirm the output `.kicad_pro` clearance.

## Out of scope (YAGNI)

- Creating a `.kicad_pro` when none exists (placement still runs; rules just
  aren't set).
- Per-net-class overrides (all classes get the same profile values).
- Persisting the fab choice across app launches.
- Editing the `.kicad_pcb` design block directly (KiCad reads rules from
  `.kicad_pro`).
