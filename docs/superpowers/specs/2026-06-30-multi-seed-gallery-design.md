# Multi-seed candidate gallery — design

**Date:** 2026-06-30
**Status:** Approved

## Problem

Placement is deterministic per seed, and different seeds produce meaningfully
different layouts (the system board ranges −29% to −44% HPWL across seeds 0–4).
Today the app runs a single seed (from a number box) and the user has no way to
compare alternatives. They want to generate several placements, preview them all,
and pick the best by eye + metrics.

## Solution overview

"Run AutoPlacement" generates **6 candidate placements** (seeds 0–5) and shows
them as a preview gallery. The user clicks the candidate they like; it becomes the
chosen board (written as `<stem>.autoplaced.kicad_pcb`, shown in the main board
view + results dashboard, with Refine enabled). The seed input box is removed —
seeds become an internal detail.

Decisions locked during brainstorming:
- **Fixed count: 6** (seeds 0–5). No user-facing count control.
- **Placement metrics only** per preview (HPWL, crossings, Δ vs. hand layout) —
  fast, no FreeRouting per candidate. The user routes only the chosen one (Refine).
- **Selection commits** the chosen seed via the existing single-seed `place` path.

## Architecture

Three thin pieces; everything else is reused.

### 1. Engine / CLI — new `place-multi` subcommand (no engine changes)

`cli.py place-multi IN.kicad_pcb [count]` (default count = 6):

- loads the board **once** via `kicad_io.load_board`,
- reads the connector sidecar via the existing `_read_connectors`,
- for each seed `k` in `0..count-1`:
  - **deep-copies the loaded model** (`copy.deepcopy`) so each seed places from a
    clean slate — `engine.place` mutates component positions in place,
  - calls the existing `engine.place(model_copy, seed=k, strategy=…,
    connectors=…)` unchanged,
  - emits one NDJSON line:
    ```json
    {"type":"candidate","seed":k,"index":i,"count":N,
     "hpwl_mm":…,"crossings":…,"overlaps":…,"hpwl_delta_pct":…,
     "board": { …serialize.board_to_dict(model_copy)… }}
    ```
- emits a terminal `{"type":"done","count":N}` line.

It **writes no `.kicad_pcb` / project files** during preview — pure
measure-and-serialize. `STRATEGY` env var is honored exactly as in `cmd_place`.
Streaming is always on for this subcommand (it only exists to feed the app).

Candidate metric sources (from `engine.place`'s return dict):
`hpwl_mm` ← `report["after"]["hpwl_mm"]`, `crossings` ← `report["after"]["crossings"]`,
`overlaps` ← `report["overlaps_remaining"]`, `hpwl_delta_pct` ← `report["hpwl_delta_pct"]`.

The seed-loop body is factored into a pure-Python helper
`autoplace/multiseed.py::run_candidates(model, count, *, strategy, connectors)`
that yields candidate dicts (no I/O, no pcbnew), so it is unit-testable on plain
Python. `cmd_place_multi` is the thin pcbnew/stdout wrapper around it.

### 2. Commit reuses the existing single-seed path

Because placement is deterministic per seed, re-running `place` with `seed=K`
reproduces the exact layout previewed for candidate K. "Use this candidate"
therefore calls the **existing** `runPlace` with `{ seed: K }`, which already:
writes `<stem>.autoplaced.kicad_pcb`, copies the `.kicad_pro`, fills the results
dashboard, and enables Refine. No new commit logic; preview == saved by
construction.

### 3. App

- **main.js:** new `run-place-multi` IPC handler that spawns
  `cli.py place-multi <board> 6` with `AUTOPLACE_STREAM=1` + `STRATEGY`, tracked in
  `activeProc` (so Cancel works), forwarding each NDJSON line as a `place-event`.
  Mirrors the existing `runPlace`/`runRefine` spawn+stream plumbing.
- **preload.js:** expose `runPlaceMulti(opts)`.
- **renderer.js:** "Run AutoPlacement" calls `runPlaceMulti`. A responsive grid of
  mini board-canvases renders one card per `candidate` event as it arrives
  (progressive). Each card reuses the existing `renderBoard` geometry rendering at
  thumbnail scale and is captioned with HPWL (mm), crossings, and Δ% vs. hand
  layout. The best (lowest) HPWL card is badged "best". Clicking a card calls the
  existing commit path (`runPlace` with that seed) and reveals the results
  dashboard for the chosen board.
- **index.html / styles.css:** a `#gallery` section (hidden until the first
  candidate) with a CSS grid of `.cand` cards; remove the seed `.field`.

## Data flow

```
Run click
  → runPlaceMulti({ board, strategy })
  → cli.py place-multi  (load once, loop 6 seeds, deepcopy+place each)
  → 6× {type:candidate, seed, metrics, board geometry}  (streamed)
  → renderer paints 6 thumbnail cards progressively, badges best HPWL
User clicks card K
  → runPlace({ board, seed:K, strategy })   (existing path)
  → writes .autoplaced.kicad_pcb, results dashboard, Refine enabled
```

## Error handling

- A seed that throws inside `engine.place` is caught in `run_candidates`; that
  candidate is emitted as `{"type":"candidate-error","seed":k,"error":…}` and the
  loop continues, so one bad seed never kills the gallery. The card renders as a
  failed tile (no thumbnail, error caption) and is not selectable.
- Cancel mid-run kills the `place-multi` process via the existing `killTree`
  path; cards already received remain selectable.
- If zero candidates succeed, the gallery shows an error message and the log holds
  the stderr, matching today's failure UX.

## Testing

Pure-Python unit tests in `tests/test_multiseed.py` (run on system python, like the
other engine tests — no pcbnew), exercising `multiseed.run_candidates` against a
small synthetic `Board`:
1. `count=6` yields 6 candidate dicts, each with keys
   `seed, hpwl_mm, crossings, overlaps, hpwl_delta_pct, board`.
2. Seeds are `0..5` in order and `board` geometry differs between at least two
   seeds (variety guard).
3. Running the same seed twice yields identical `board` geometry (determinism
   guard).
4. A seed whose placement raises yields a `candidate-error` entry and does not
   abort the remaining seeds.

UI (gallery rendering, progressive cards, click-to-commit) is verified manually in
the app against the system board.

## Out of scope (YAGNI)

- User-configurable candidate count.
- Per-candidate FreeRouting / routed-% (the chosen board is routed via Refine).
- Persisting/comparing candidates across runs.
- Parallel seed execution (6 sequential placements are fast enough; revisit only
  if the system board feels slow).
