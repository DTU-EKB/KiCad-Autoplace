# Aesthetic alignment post-pass

**Date:** 2026-06-30
**Status:** Design approved (scope + exposure chosen by user); ready for implementation plan.
**Roadmap context:** First Phase-3 increment ("aesthetic post-passes"). Ships as one
FreeRouting-gated increment, exactly like the Phase 2 terms.

---

## 1. Goal

Make placements *read as hand-laid by a senior engineer* by lining parts up. After
`legalize`, every part is grid-snapped independently, so nothing lines up with anything
else — rows of decaps / resistors sit at slightly different coordinates. The post-pass
snaps **near-collinear related parts onto a shared axis** (the single biggest visual cue
of a tidy board), without disturbing routability.

**Scope v1 (user-chosen): alignment only.** No even-spacing, no decap-offset snap, no
rotation changes (rotations are already cardinal, and 2C deliberately orients connectors
— forcing uniform rotation would fight it). Those are possible later increments.

## 2. Non-negotiable invariants

1. **Legality-preserving.** A snap is applied to a part only if, after the move, the part
   has no courtyard overlap with any other part (same `(eff_w+eff_w)/2 + margin` test
   `legalize.push_apart` uses) and stays inside the outline (`geom.clamp_center` bounds).
   Otherwise that part is left exactly where `legalize` put it. The post-pass can therefore
   never make a board *less* manufacturable than `legalize` already guaranteed.
2. **Routing-neutral by construction.** Every move is bounded by a small tolerance
   (`ALIGN_TOL_MM = 1.5`), so no part travels far. Validated by a FreeRouting
   non-regression gate before merge (system + motor_power), like every prior term.
3. **Deterministic.** Sorted iteration, fixed tolerance, grid-snapped targets. Same input →
   same output. No RNG.
4. **`anneal._quality` is untouched.** This is a geometric post-pass after the SA loop, not
   a cost term. (Invariant carried from the roadmap.)
5. **Off → identical to today.** With the flag off, `engine.place` returns the exact
   `legalize` result (byte-for-byte). The post-pass is purely additive.
6. **Pure module.** `aesthetic.py` imports only `geom`/`metrics`/`model` (no pcbnew),
   unit-testable on plain python.

## 3. Scope — four pieces

| # | Piece |
|---|---|
| **G1** | `aesthetic.align(board, *, grid, margin, tol=ALIGN_TOL_MM) -> int` — the post-pass. Returns #parts moved. New pure module `plugin/plugins/autoplace/aesthetic.py`. |
| **G2** | `engine.place(..., aesthetic: bool = True)` — run `aesthetic.align` as the final stage, after `legalize`. Report carries `aligned_parts`. |
| **G3** | `metrics.alignment_score(board, tol=ALIGN_TOL_MM) -> float` — mean residual of clusterable parts from their shared axis (mm; lower = tidier; 0.0 when nothing is clusterable). Gate + report metric only — **NOT** folded into `ranking.candidate_key` (the audit's dilution warning; mirrors how `tall_clearance` stayed out). |
| **G4** | CLI passthrough: `cmd_place` / `cmd_place_multi` / `_route_candidate` read `os.environ.get("AESTHETIC", "1") != "0"` and pass `aesthetic=` to `engine.place`. Default ON (env unset → on). The desktop app can set `AESTHETIC=0` to disable. |

### Out of scope (deferred)
- App UI toggle widget. Default-ON delivers the benefit in the app immediately; a checkbox
  that sets `AESTHETIC=0` is a small follow-on in the Electron layer, not this increment.
- Even-spacing / redistribution; decap-offset snap; rotation consistency.
- `alignment_score` on the gallery card (kept off the card to preserve auditability).

## 4. Current state (verified against `main` @ 90b4f76)

- `engine.place` (engine.py:19,88) ends with `remaining = legal_mod.legalize(board, grid=grid,
  margin=margin)` then builds the report. **G2 inserts `aesthetic.align` between legalize and
  the report**, and threads `aesthetic` through the signature.
- `legalize.legalize` (legalize.py:65) already grid-snaps every free non-edge part and
  push-aparts. The post-pass runs *after* it and re-snaps its own targets to grid.
- `legalize.push_apart` (legalize.py:25-62) holds the exact overlap predicate to reuse:
  `ox = (a.eff_w+b.eff_w)/2 + margin - abs(a.x-b.x)`, `oy = ...`, overlap iff `ox>0 and oy>0`.
- `geom.clamp_center(c, board, margin)` (geom.py) is the bounds clamp; reuse for the
  in-bounds check (clamp a trial copy and see if it moved, or bounds-test directly).
- `Component`: `x`, `y`, `rot` (0/90/180/270), `eff_w`/`eff_h` (rotation-aware), `edge`
  (""=free), `locked`, `block`. `board.free()` = movable parts. Candidate set =
  `board.free()` minus `c.edge` minus `c.locked`.
- `metrics.py` holds the pure-proxy pattern (`sheet_spread_score`, `decap_proximity`,
  `tall_clearance`); **G3 adds `alignment_score` beside them.**

## 5. Design

### G1 — `aesthetic.align`

```python
ALIGN_TOL_MM = 1.5   # max distance two parts' centres may differ on an axis and still
                     # be snapped to a shared line; also the max any part is moved.

def align(board, *, grid=0.5, margin=0.8, tol=ALIGN_TOL_MM) -> int:
    """Snap near-collinear free parts onto a shared axis, per functional block.
    Legality-preserving (no new overlap, stays in bounds) and deterministic.
    Returns the number of parts actually moved."""
```

Algorithm (X axis, then Y axis, on the updated positions):
1. **Candidates:** `[c for c in board.free() if not c.edge and not c.locked]`.
2. **Group by `c.block`** (parts on the same sheet are the ones a reader expects to line up;
   on a flat board every part shares one block, which is fine). Groups sorted by key for
   determinism.
3. For `axis in ("x", "y")`:
   a. Within each group, sort parts by the axis coord (ties broken by `ref`).
   b. **Cluster** the sorted coords greedily: start a cluster with the first part; each next
      part joins iff its coord is within `tol` of the cluster's *running mean*; else it
      starts a new cluster.
   c. For each cluster of **≥ 2** parts: `target = _snap(mean(coords), grid)`.
      For each part in the cluster (sorted by `ref`), **attempt** to set its axis coord to
      `target` via `_try_move` (below). Accept iff legal, else leave the part unchanged.
4. Return the count of accepted moves.

`_try_move(board, c, axis, target, margin)`:
- Save `old = getattr(c, axis)`. If `abs(target-old) < 1e-9`: return False (no-op).
- Set the coord to `target`. **In-bounds:** if `c.left < board.x0+margin or c.right >
  board.x1-margin or c.top < board.y0+margin or c.bottom > board.y1-margin` (using the same
  inset `geom.clamp_center` applies, i.e. `margin + board.edge_keepout`) → revert, return
  False. **Overlap:** for every other component `o` (≠ c), if `(c.eff_w+o.eff_w)/2+margin >
  abs(c.x-o.x)` and `(c.eff_h+o.eff_h)/2+margin > abs(c.y-o.y)` → revert, return False.
- Else keep, return True.

Doing X first then Y catches both columns (parts sharing X) and rows (parts sharing Y);
a part may be eligible on both axes and is snapped on whichever cluster accepts. Because
each accepted move is overlap-checked against current positions, the result is overlap-free
by construction — no extra `push_apart` needed. (Targets are grid-snapped, so output stays
on-grid.)

### G2 — wire into `engine.place`

After `remaining = legal_mod.legalize(...)` (engine.py:88), before the report:

```python
    aligned = 0
    if aesthetic:
        aligned = aesthetic_mod.align(board, grid=grid, margin=margin)
    _report("aesthetic", 0.98)
```

Add `aesthetic: bool = True` to the signature; add `"aligned_parts": aligned` to the report
dict. Import `aesthetic as aesthetic_mod`. With `aesthetic=False`, `align` is never called and
the board is the verbatim `legalize` result (invariant 5).

### G3 — `metrics.alignment_score`

```python
def alignment_score(board, tol=ALIGN_TOL_MM) -> float:
    """Mean residual (mm) of clusterable free parts from their block's shared axis.
    Lower is better; 0.0 when no block has >=2 parts within tol on an axis.
    Same grouping/clustering as aesthetic.align, so metric and term stay in lockstep."""
```

For each block, on each axis, reuse the same greedy clustering; for every cluster of ≥2,
accumulate `abs(coord - mean(cluster))` per part; return the mean over all such parts (0.0
if none). Pure; uses `ALIGN_TOL_MM` so the metric measures exactly what the term optimizes.

### G4 — CLI passthrough

In `cmd_place`, `cmd_place_multi`, and `_route_candidate`, compute
`aesthetic = os.environ.get("AESTHETIC", "1") != "0"` and pass it into every `engine.place`
call. Default ON. No new positional args (mirrors the `STRATEGY` env-var convention).

## 6. Validation

**Pure unit tests (plain pytest):**
- `tests/test_aesthetic.py`:
  - Three parts in one block at x = 10.0, 10.4, 11.2 (all within `tol` of the running mean),
    well separated in y → after `align` all three share one grid-snapped X; returns 3.
  - A part whose snap would overlap a neighbor is **left unmoved** (move rejected); the
    aligned others still snap.
  - A part whose snap would cross the outline margin is **left unmoved**.
  - Parts farther apart than `tol` are **not** merged into one cluster (two clusters → two
    distinct target lines, or singletons left alone).
  - `edge`/`locked` parts are never moved.
  - Determinism: two runs on a deep-copied board give identical coordinates.
- `tests/test_metrics_proxies.py`: `alignment_score` is lower after `align` than before on a
  clusterable board; `0.0` when every block has < 2 within-tol parts.
- `tests/test_engine.py`: `place(..., aesthetic=False)` equals the pre-existing legalize
  result (positions unchanged vs a baseline run); `place(..., aesthetic=True)` leaves **zero
  overlaps** (`metrics.overlaps(board) == []`) and every part still in bounds.

**FreeRouting gate (KiCad python, `scratchpad/route_baseline.py`, strip tracks):**
- Baseline = `main` @ 90b4f76 (aesthetic off): system 97.8% / motor_power 65.3% (measured
  this session at DECAP=3.0).
- After G2 (aesthetic on): re-place + route the two boards; **require routed-% ≥ baseline −
  ~1% (no regression)** and **`alignment_score` strictly lower** on a board with clusterable
  parts (system). Record both. If routing regresses, lower `ALIGN_TOL_MM` and re-gate.
- Secondary sanity: `decap_proximity` on system must not jump (moves ≤ tol ⇒ expect ≈flat).

## 7. Risks

- **R1 — a chain of within-tol parts drifts the cluster mean** so the first and last differ
  by > tol, making a large move. Mitigated: the *move* is `target - coord`; with the mean as
  target and members within tol of the running mean, no member is more than ~tol from target.
  The bounds/overlap guard rejects any pathological move anyway.
- **R2 — alignment vs the tuned decap proximity.** A cap could be nudged ≤ tol from its IC.
  Negligible vs the 15 mm proximity scale; the gate's secondary `decap_proximity` check
  confirms. (Caps in the same block tend to share an axis *with* their IC anyway, so
  alignment usually reinforces decap tucking.)
- **R3 — flat single-sheet board = one big block** → every part is a clustering candidate.
  Still safe (overlap-guarded), and lining up a flat board is exactly the desired effect.

## 8. Build order

G1 (`aesthetic.align` + `ALIGN_TOL_MM` + tests) → G3 (`alignment_score` + tests) →
G2 (`engine.place` wiring + report + tests) → G4 (CLI passthrough) → FreeRouting gate.
G2 after G1/G3 so the gate measures the real pipeline; G4 last (thin glue).
