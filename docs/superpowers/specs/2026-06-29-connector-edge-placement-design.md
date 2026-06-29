# Connector graphical selection + edge placement

Status: approved design (2026-06-29). Implementation not started.

## Problem

The autoplacer minimises HPWL and MST crossings, which improved the numbers but
produces visually illogical boards: connectors sit mid-board, orientations look
random, and functional groups read as a centroid blob rather than islands. The
first concrete structural fix the user asked for: **graphically pick which
footprints are connectors, then have the engine place them on the board edges
where they make most sense**, giving the rest of the placement a frame to
organise around.

This spec covers only the connector feature. The next, separate cycle is
"decap-near-IC + grouping rules" (the user's chosen follow-up) and is out of
scope here.

## Goals

- A clickable board view in the Electron app to select/deselect connectors.
- Selected connectors are placed on the board edge nearest the circuitry they
  feed, and slide along that edge during optimisation to minimise wirelength.
- The board view doubles as a placement viewer (see results without opening
  KiCad).
- Connector selection persists between runs, without modifying the `.kicad_pcb`.

## Non-goals (this cycle)

- Rendering traces / copper / ratsnest on the canvas — courtyards + refdes only.
- Decap-near-IC and functional-grouping rules (next cycle).
- Pad-facing-direction fine-tuning beyond a simple along-edge orientation.
- Multi-board, undo history beyond the live toggle set.

## Architecture

Keep `pcbnew` isolated in `kicad_io`; keep the engine pure-Python and testable.

### Engine / CLI

- `cli.py dump <board>` *(new subcommand)* — load the board, emit JSON:
  `{outline:{x0,y0,x1,y1}, footprints:[{ref,x,y,w,h,rot,block,sheet,
  is_connector_guess,locked,pads:[{net,ox,oy}]}]}`. Reuses
  `kicad_io.build_model` + `blocks.detect_blocks` + a serializer. This is what
  the canvas renders. Generalises the geometry dump already prototyped for the
  hand-vs-auto comparison image.
- `autoplace/edge.py` *(new, pure-Python, unit-tested)* — connector→edge
  assignment and the slide-along-edge constraint helpers (see Algorithm).
- `anneal.py` — an edge-assigned connector moves only **1-D along its assigned
  edge**: its nudge perturbs the along-edge coordinate, and the clamp keeps it on
  the edge line. It never drifts inward. Distinct from `locked` (locked = never
  moves). Rotation for an edge connector is fixed to the edge-appropriate
  orientation. Non-connectors and the existing quality-selection logic are
  unchanged.
- `engine.place(board, ..., connectors=None)` — when `connectors` (a set of
  refdes) is given it overrides the `is_connector` auto-guess. Pipeline becomes:
  seed non-connectors → assign connectors to edges (`edge.py`) → SA (connectors
  slide on edges, rest free) → legalize.
- `cli.py place` — reads the connector set from the sidecar (path derived from
  the input board) and passes it to `engine.place`.

### App

- `preload.js` / `main.js` — new IPC:
  - `dump-board(board, python)` → spawns `cli.py dump`, returns geometry JSON.
  - `load-connectors(board)` → reads sidecar (or returns auto-guess from the
    dump).
  - `save-connectors(board, refs)` → writes sidecar.
- `renderer` — an SVG board canvas: footprint courtyards coloured by block,
  refdes labels, connectors visually distinct. Clicking a footprint toggles its
  connector flag and saves the sidecar. A small legend/count shows the current
  set. The existing python/board pickers, run button, progress bar, and metrics
  dashboard stay. After a run, the canvas re-renders from the result geometry.

### Persistence

`<board-stem>.autoplace.json` next to the board:
`{"connectors": ["J_PV101", "TP108", ...]}`. Never touches the `.kicad_pcb`. On
open: load it; if absent, pre-fill from the dump's `is_connector_guess`. On
toggle: save.

## Data flow

```
open board → dump-board → render canvas → load sidecar (or auto-guess) → highlight connectors
click parts → toggle set → save sidecar
Run → cli.py place reads sidecar → engine: seed → assign connectors to edges → SA → legalize
     → stream progress/result (result carries new geometry) → canvas shows the result
```

## Edge-assignment algorithm (`edge.py`)

1. **Seed first.** Seed the non-connector parts (floorplan / force-directed) so
   there is a layout to reason about.
2. **Pick the edge per connector.** Compute the centroid of each connector's
   connected non-power net partners. Choose the edge (left/right/top/bottom)
   nearest that centroid, so the connector sits on the edge closest to the
   circuitry it feeds (short wiring, pulled outward).
3. **Place on the edge line.** Snap the connector's courtyard against that edge
   (inside the margin), oriented with its long axis along the edge (rot 90/270
   for left/right, 0/180 for top/bottom). The along-edge coordinate starts at the
   projection of its net centroid onto the edge.
4. **De-collide.** Connectors on the same edge are sorted by along-edge
   coordinate and spaced so courtyards do not overlap.
5. **Slide during SA.** Each connector is tagged with its edge. The annealer
   perturbs only the along-edge coordinate; the clamp keeps it on the edge line.

Result: connectors form a fixed perimeter frame, optimised along each edge, and
the interior places around them.

### Edge-constraint representation

Add an `edge: str = ""` field to `Component` (`""` = free, `L`/`R`/`T`/`B` =
edge-pinned). `edge.py` sets it; `anneal.py` reads it to restrict moves; the
legalizer must respect it (never push an edge connector off its edge). Keeping
the constraint on the model (not a side table) means metrics, snapshot/restore,
and legalize all see it consistently.

## Error handling

- `dump-board` failure → surface via the existing log/error path in the renderer.
- No connectors selected → engine runs as today (no edge frame).
- Sidecar missing/corrupt → fall back to auto-guess; never crash the run.
- A connector with no signal net partners → assign to the nearest edge by its
  current position (no centroid available).

## Testing

- `edge.py` (pure-Python): a connector wired to parts on the right is assigned
  edge R; after assignment every connector courtyard touches its edge within the
  margin; connectors on one edge do not overlap; a connector with no signal nets
  still gets a valid edge.
- `anneal.py` regression: after annealing, every edge connector is still on its
  edge line (slid along, not drifted inward); non-connectors unaffected.
- `cli.py dump` smoke (KiCad python): emits valid JSON with the expected keys on
  the system board.
- App canvas: manual verification — launch, toggle connectors, run, view result.

## Open follow-ups (not this cycle)

- Decap-near-IC + functional grouping rules (next cycle; the chosen priority for
  "make it look logical").
- Rotation/alignment discipline for the interior.
