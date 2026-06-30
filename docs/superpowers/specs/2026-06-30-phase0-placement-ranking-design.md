# Phase 0 — Placement ranking, cheap proxies, edge-keepout & cross-block gutters

**Date:** 2026-06-30
**Status:** Design approved; ready for implementation plan.
**Roadmap context:** This is **Phase 0** of the placement-quality roadmap distilled from
two independent AI audits of the auto-placement engine (notes in the placement-audit
synthesis). Phase 0 is the "zero-new-data, biggest-feels-less-random-per-effort" slice:
it changes *ranking, measurement, and a small amount of placement spacing* — it does
**not** touch the data model's electrical intent (component value, pad type, net class),
which is Phase 1.

---

## 1. Goal

Stop trusting HPWL as the sole arbiter of "best placement," and give the multi-seed
gallery a deterministic, explainable ranking grounded in the project's own validated
finding that *spread routes better than HPWL-minimal* (86.6% vs 76.3% FreeRouting on the
131-part `system` board). Surface the new signals to the user, route the finalists for a
real routability number, and add two small spacing controls that make boards read as
organized regions.

## 2. Background — why now

The engine produces overlap-free, low-HPWL, deterministic layouts that nonetheless read
as *scattered*. Two audits independently traced this to:

1. **`anneal._quality` degeneracy** — the kept layout minimizes `HPWL(signal) + overlap`
   and *nothing else*; HPWL has a large near-optimal plateau, so the specific layout
   returned is arbitrary with respect to spread, alignment, and routability.
2. **HPWL is not routability** — the project already proved (`BUILD_SPEC.md:391-405`) that
   the lowest-HPWL layout routed *worst*. The multi-seed gallery currently ranks by HPWL
   (`renderer.js:markBestCandidate`), so it badges the wrong candidate "best."

Phase 0 fixes the *selection/ranking* layer (cheap, no data-model change) and adds two
spacing controls (`edge_keepout`, cross-block gutters). It deliberately does **not** add
soft terms to `anneal._quality` — that split is hard-won and load-bearing
(`BUILD_SPEC.md:368-379`); ranking moves *out* to the multiseed layer instead.

## 3. Scope — five deliverables

| # | Deliverable |
|---|---|
| **D1** | Two-level lexicographic candidate ranking + auto-route the top-2 (new `ranking.py`, `cli.py`, `multiseed.py`, app IPC + gallery). |
| **D2** | Three cheap proxies (`sheet_spread_score`, `pinch_fraction`, `whitespace_connectivity`) added to each candidate dict and shown on every gallery card. |
| **D3** | `Board.edge_keepout` (default `0.0`) folded into a single shared clamp helper used by all three placement phases. |
| **D4** | Fab-derived routing channel + cross-block whitespace gutter, scaled by the existing `channel_scale`. |
| **D5** | Fix the stale rotation docstring in `engine.py`. |

### Out of scope (later phases)
- Data-model enrichment (component value, pad electrical type, net class) — **Phase 1**.
- Any electrical-aware cost term (decap proximity, crystal hug, aggressor/victim
  separation, commutation loops) — **Phase 2+**.
- Any change to `anneal._quality` — explicitly forbidden; ranking lives in the multiseed
  layer.

---

## 4. Guiding invariants

1. **Defaults reproduce today exactly — except D4.** `edge_keepout=0.0` and the ranking
   fallback (`RANK=hpwl`) are byte-identical to current behavior. D2 proxies are additive
   fields ignored by current consumers. **D4 is the one intentional layout change** (the
   gutter is on by default — it *is* the deliverable) and is validated by a FreeRouting
   non-regression rather than by an identity check.
2. **`anneal._quality` is untouched.** No electrical or aesthetic term is added to the
   selection metric. Ranking is computed in the multiseed layer, over whole candidates.
3. **Determinism per seed.** Every proxy is a pure function of board geometry; the ranking
   key is a total order (seed is the final tiebreak); FreeRouting passes and the `-de`
   seed are pinned so a candidate routes identically every run.
4. **Pure-Python engine boundary preserved.** Proxies and ranking are `pcbnew`-free and
   unit-tested on plain Python. Only the route-top-2 step touches the existing
   KiCad/FreeRouting path, exactly as routing already does.
5. **Graceful degradation.** If FreeRouting/Java is unavailable, the gallery still works:
   it emits `route-skipped` and the proxy ranking stands.

---

## 5. Current behavior (verified against `main @ dcb3511`)

- **`multiseed.run_candidates(model, count, *, strategy, connectors, margin=0.8)`**
  (`plugin/plugins/autoplace/multiseed.py:19`) — deep-copies the model per `seed in
  range(count)`, calls `engine.place(...)`, and *streams* one candidate dict per seed:
  `{type:"candidate", seed, hpwl_mm, crossings, overlaps, hpwl_delta_pct, board}`. On a
  bad seed it yields `{type:"candidate-error", seed, error}` and continues. **No sort
  anywhere.**
- **`cli.py:cmd_place_multi` (`cli.py:95`)** — iterates `run_candidates`, tags each with
  `index`/`count`, emits a `progress` event then the candidate, then a final `done`.
  Streaming is per-seed and immediate.
- **`engine.place` (`engine.py:19`)** — computes `util = used/area` and
  `channel_scale = max(0.0, min(1.0, (0.55 - util)/0.35))` (`engine.py:60`); returns a
  report including `channel_scale` and `overlaps_remaining`.
- **`metrics.py`** — `hpwl` (line 37) and `crossings` (line 93) exclude power nets;
  `overlaps` (line 116) returns overlapping `(refA, refB)` pairs; `summary` (line 130)
  returns `{hpwl_mm, crossings, overlaps, components}`. All pure.
- **`congestion.py`** — builds a cell grid over the board outline
  (`nx = ceil(width/cell_mm)`, origin `board.x0,y0`) and parses FreeRouting `.ses` into a
  pressure field; exposes an `empty` flag for the no-routing case.
- **`anneal.py`** — `_Weights` (line 31): `HPWL=1.0, OVERLAP=60.0, EDGE=0.6,
  COHESION=0.35, CHANNEL=4.0, CONG_K=3.0`; `CHANNEL_MM=2.6` (line 41, hardcoded =
  `1.0 track + 2×0.8 clearance`, laser-only). `Annealer.__init__` stores the *product*
  `self.channel = CHANNEL * channel_scale` (line 51) but **not** the raw `channel_scale`.
  `_pair_penalty` (line 85) applies the channel term with **no `a.block != b.block`
  branch**. `Annealer._clamp` (line 163) clamps a center to the outline inset by
  `self.margin`.
- **Three duplicated clamps** with identical arithmetic
  `c.x = min(max(c.x, x0+hw+margin), x1-hw-margin)`: `legalize._clamp` (`legalize.py:20`),
  `forcedirected._clamp_to_board` (`forcedirected.py:18`), `Annealer._clamp`
  (`anneal.py:163`).
- **`model.Board` (`model.py:78`)** — `Board(x0, y0, x1, y1, components)`; no
  `edge_keepout`. `Component` has `sheet`, `block`, `eff_w/eff_h`, `left/right/top/bottom`.
- **`fabrication.py`** — `PROFILES = {"laser": {clearance:0.8, track:1.0},
  "cnc": {clearance:0.85, track:1.0}}`; `margin_for(fab)` returns the profile clearance.
- **App** — `renderer.js:addCandidateCard` (line 412) renders a single metrics line
  (`HPWL mm Δ% · crossings`); `markBestCandidate` (line 448) badges the HPWL-min card;
  `main.js:runPlaceMulti` (line 239) spawns `place-multi` and forwards each NDJSON line
  via `place-event`.

---

## 6. Design

### D5 — Stale rotation docstring (do first; trivial)

`engine.py:4` currently reads:

> `(Rotation moves remain the last open M4 item; this pass is translation + swap.)`

Rotation is implemented (`anneal.py` rotate/swap moves; `kicad_io.apply_to_board` applies
`rot`). Replace the module docstring's pipeline line with an accurate one, e.g.:

> `Pipeline: detect blocks -> seed -> force-directed global -> SA refine (translation, rotation, swap) -> legalize.`

Pure comment change. No test impact.

### D3 — `Board.edge_keepout` + shared clamp helper

**Model.** Add to `model.Board`:

```python
edge_keepout: float = 0.0   # extra inward inset from the outline (mm); 0 == legacy
```

**Shared helper.** Introduce one clamp function and call it from all three phases so they
can never diverge again. Recommended home: a new `plugin/plugins/autoplace/geom.py` (small,
focused) or `model.py`. Signature:

```python
def clamp_center(c, board, margin):
    """Clamp component center so its eff-bbox stays inside the outline, inset by
    margin + board.edge_keepout on every side."""
    inset = margin + board.edge_keepout
    hw, hh = c.eff_w / 2, c.eff_h / 2
    c.x = min(max(c.x, board.x0 + hw + inset), board.x1 - hw - inset)
    c.y = min(max(c.y, board.y0 + hh + inset), board.y1 - hh - inset)
```

Replace the bodies of `legalize._clamp`, `forcedirected._clamp_to_board`, and
`Annealer._clamp` with calls to this helper. (`Annealer._clamp` passes `self.board`,
`self.margin`.)

**Subtleties (no-ops at default 0, but specified so the implementer handles them):**
- `legalize.push_apart` overlap *detection* uses `margin` for inter-part spacing — leave
  it; `edge_keepout` is an *edge* inset only.
- `forcedirected.seed_positions` seeds inside a `margin`-inset box then clamps; with
  `edge_keepout > margin` a seed could start inside the keepout and be pulled in by the
  clamp — acceptable. Optionally fold `edge_keepout` into `usable_w/h` for clean seeding;
  irrelevant at default 0.

**Default reproduces today.** `edge_keepout=0.0` ⇒ `inset == margin` ⇒ byte-identical.

### D4 — Fab-derived channel + cross-block gutter

**Concept.** Three distinct spacings, kept separate:
- **Copper clearance** (0.8 laser / 0.85 CNC) — hard fab minimum; unchanged, owned by the
  fab profile. `margin` already equals this clearance in the engine.
- **Routing channel** = room for one track between two parts = `track + 2×clearance`.
- **Cross-block gutter** = one *extra* track of room between parts in different blocks =
  `track + clearance`, scaled by `channel_scale`.

**Thread the track width to the annealer.** Add a `track: float = 1.0` parameter to
`engine.place` and pass it into `Annealer.__init__`. `cli.py` sources it from the active
fab profile (`fabrication.PROFILES[fab]["track"]`), alongside the clearance it already
passes as `margin`. Non-fab callers (tests) get `track=1.0`, `margin=0.8`.

**Single channel-width helper** (shared by the annealer *and* the `pinch_fraction` metric
so they never drift):

```python
def channel_width(margin, track):
    """Clear gap (mm) that fits one routing track between two courtyards:
    clearance + track + clearance, where margin == clearance."""
    return track + 2 * margin
```

**Annealer changes (`anneal.py`):**
- Store the raw scalar: `self.channel_scale = channel_scale` (currently only the product
  `self.channel` is kept).
- Replace the module constant `CHANNEL_MM` with an instance value
  `self.channel_mm = channel_width(self.margin, track)` (= 2.6 at laser defaults; 2.7 at
  CNC — fixes the latent CNC bug).
- Gutter: `self.gutter = track + self.margin`.
- In `_pair_penalty`, compute the target per pair:

```python
target = self.channel_mm
if a.block and b.block and a.block != b.block:
    target += self.gutter * self.channel_scale
if self.channel and shadow and 0 <= gap < target:
    cost += local * (target - gap)
```

**Density protection.** On dense boards `util ≥ 0.55 ⇒ channel_scale = 0`, so the gutter
term vanishes and cross-block pairs fall back to the plain channel target — `motor_power`
is protected by construction.

**This changes layouts** (intended). Validated by §8 gates G4/G5, not by an identity check.

### D2 — Three cheap proxies

All three are pure functions added to `metrics.py`, computed in `run_candidates` from the
*post-placement* board just before each yield, and added to the candidate dict. Each
proxy's docstring states its handling of power/locked/edge parts (mirroring how `hpwl`
documents power exclusion).

**`sheet_spread_score(board) -> float`** — group `board.components` by `.sheet`; for each
non-empty sheet, fill ratio `sum(eff_w*eff_h) / sheet_bbox_area`; aggregate (mean) across
sheets. **Excludes edge-pinned connectors (`c.edge`) and locked parts** from the bbox, so
boards with many edge connectors aren't penalized. Lower = each sheet occupies a clean,
appropriately-filled region. Boards with `< 2` sheets return a defined sentinel (e.g.
`0.0`) so single-sheet boards rank purely on the remaining keys.

**`pinch_fraction(board, margin, track) -> float`** — over neighbor pairs that shadow on
one axis (`min(gx, gy) < margin`), the fraction whose perpendicular gap
`0 <= max(gx, gy) < channel_width(margin, track)`. Uses the **shared** `channel_width`
helper so it stays in lockstep with `_pair_penalty`. Lower = fewer routing pinch points.
Denominator is the count of shadowing pairs (or `1` if none, returning `0.0`).

**`whitespace_connectivity(board, cell_mm=...) -> float`** — `cell_mm` defaults to the
same cell size `congestion.py` uses for its grid (read it from there; do not introduce a
second magic number). Rasterize component courtyards (`left/right/top/bottom`) onto a grid
over the outline (reuse the grid math from `congestion.py`), flood-fill the empty cells,
return
`largest_empty_region / total_empty_cells`. `1.0` = whitespace is one connected routing
sea; low = fragmented pockets. Flood-fill order does not affect the size result
(deterministic). If there are no empty cells, return `0.0`.

**Candidate dict.** `run_candidates` adds `sheet_spread_score`, `pinch_fraction`,
`whitespace_connectivity` to each `candidate` dict. `margin` and `track` are already
available in `run_candidates`/`cmd_place_multi`.

### D1 — Two-level ranking + auto-route the top-2

**New pure module `plugin/plugins/autoplace/ranking.py`** — unit-testable with mock
candidate dicts, no engine dependency:

```python
def candidate_key(cand: dict) -> tuple:
    """Lexicographic pre-rank key; lower is better on every component.
    Pure function of the candidate dict; floats rounded so cross-machine FP noise
    cannot flip the order; `seed` last gives a total order (no nondeterministic ties)."""
    return (
        cand["overlaps"],                              # legal layouts win outright
        round(cand["sheet_spread_score"], 3),          # clean per-sheet spread
        round(cand["pinch_fraction"], 3),              # fewer pinch points
        round(cand["hpwl_mm"], 2),                     # HPWL is the final metric tiebreak
        cand["seed"],                                  # total order
    )

def pre_rank(candidates: list[dict]) -> list[dict]:
    """All candidates, best first, by candidate_key."""
    return sorted(candidates, key=candidate_key)

def final_order(candidates: list[dict], routed: dict[int, float]) -> list[dict]:
    """Two-level: routed finalists (by -routed_pct, then pre-rank key) first;
    the remaining candidates keep pre-rank order below them.
    `routed` maps seed -> routed_pct for the candidates that were routed."""
    pre = pre_rank(candidates)
    finalists = [c for c in pre if c["seed"] in routed]
    rest = [c for c in pre if c["seed"] not in routed]
    finalists.sort(key=lambda c: (-routed[c["seed"]], candidate_key(c)))
    return finalists + rest
```

> **Note on `whitespace_connectivity`.** It is surfaced on the card but is *not* in
> `candidate_key` v1 — the pre-rank uses the three strongest signals
> (`overlaps`, `sheet_spread`, `pinch_fraction`) plus HPWL, keeping the key simple and the
> ordering easy to reason about. `whitespace_connectivity` is a displayed diagnostic that
> §10 calls out as the first knob to fold in if real boards show it helps. This keeps v1
> auditable.

**Ranking lives in `cli.py`, not `run_candidates`.** `run_candidates` stays a streaming
generator so the gallery fills card-by-card. `cmd_place_multi`:
1. Streams each candidate as today (cards appear immediately).
2. Buffers a *lightweight projection* of each successful candidate
   (`{seed, overlaps, sheet_spread_score, pinch_fraction, whitespace_connectivity,
   hpwl_mm}`) — **not** the full serialized board (memory stays flat on the 131-part
   board).
3. After the stream, computes `pre = ranking.pre_rank(buffer)` and emits a `ranking`
   event with the pre-rank order and `best_seed = pre[0]["seed"]` so the gallery can
   reorder/badge immediately.
4. Routes the top-2 (see below), then emits an updated `ranking` event reflecting
   `final_order` (finalists may swap on routed-%), plus per-candidate `route-result`
   events.
5. Emits `done`.

**Auto-route the top-2 (Approach A — inline, deterministic, graceful):**
- Because `engine.place` is deterministic per seed, routing a finalist = **re-run
  `place` for that seed** and route the result via the existing KiCad/FreeRouting path
  (apply placement to the source `.kicad_pcb`, export DSN, run FreeRouting with **pinned
  passes and `-de` seed**, import the `.ses`). No need to buffer N boards.
- Compute `routed_pct` per finalist using the **existing routed-% measurement** already
  used by the route-driven refine loop / `route_check` (count of routed vs total
  connections from the imported SES). `congestion.parse` is reused only for the
  congestion field and its `CongestionField.empty` flag (the no-routing edge case).
- Emit `{type:"route-result", seed, routed_pct}` per finalist.
- **Fallback:** if FreeRouting/Java is unavailable or a route fails, emit
  `{type:"route-skipped", seed, reason}` for each finalist and skip the routed
  re-ranking; the proxy `ranking` from step 3 stands.
- A `RANK=hpwl` env flag (or absent proxies) collapses `candidate_key` to
  `(hpwl_mm, seed)` — exactly today's HPWL-min badge.

**App (`main.js` + `renderer.js`):**
- `main.js:runPlaceMulti` forwards the new `ranking`, `route-result`, `route-skipped`
  events over the existing `place-event` channel (already guarded by
  `!win.isDestroyed()`).
- `renderer.js`: `addCandidateCard` keeps streaming cards. `markBestCandidate` is
  **replaced** by a `ranking`-event handler that reorders the cards to the given order,
  badges index 0 as "recommended", and default-selects it. A `route-result` handler
  upgrades a card with a measured routed-% chip; `route-skipped` leaves the proxy
  ranking and shows no chip (or a subtle "not routed" note).
- Card metrics grow to two rows: row 1 `HPWL mm Δ% · crossings` (unchanged); row 2
  `spread · pinch% · ws% · overlaps`; plus the routed-% chip when present.
  `styles.css .cand-metrics` adapts to two rows.

---

## 7. Data flow (place-multi, end to end)

```
cli.py cmd_place_multi
  └─ run_candidates(model, N, margin, track)        # streaming
       per seed: deepcopy → engine.place → metrics + 3 proxies
       yield {type:"candidate", seed, hpwl_mm, crossings, overlaps,
              hpwl_delta_pct, sheet_spread_score, pinch_fraction,
              whitespace_connectivity, board}
  ── emit progress + candidate per seed (cards appear live) ──▶ app
  buffer lightweight {seed, overlaps, proxies, hpwl_mm}
  pre = ranking.pre_rank(buffer)
  emit {type:"ranking", order:[seeds...], best_seed}          ──▶ app reorders+badges
  for seed in pre[:2]:
     re-place(seed) → route_once(pinned) → routed_pct
     emit {type:"route-result", seed, routed_pct}             ──▶ app adds routed chip
       (or {type:"route-skipped", seed, reason} on failure)
  if any routed: emit {type:"ranking", order:final_order(...), best_seed}
  emit {type:"done", count:N}
```

---

## 8. Risks & non-regression gates

**Identity gates (must hold byte-for-byte at defaults):**
- **G1 — `edge_keepout=0.0`** ⇒ placement identical to pre-change on every test board.
  Add a test: same board, default vs explicit `edge_keepout=0` → identical positions. The
  existing suite must still pass.
- **G2 — ranking fallback** (`RANK=hpwl` / absent proxies) reproduces today's badge
  (lowest-seed min-HPWL).
- **G3 — additive candidate fields** don't break existing consumers; only
  `test_multiseed.py`'s field-shape assertion changes (to superset).

**Behavioral-change gates (D4 intends to change output; measure, don't assume):**
- **G4 — `motor_power` (dense):** confirm `channel_scale=0` zeroes the gutter; require no
  regression in `overlaps_remaining` and no HPWL blow-up vs pre-change.
- **G5 — `system` (roomy):** FreeRouting routed-% must hold or rise (this is the board
  where spread→routability was validated).
- **G6 — route-top-2:** FreeRouting completes in acceptable wall-clock on `system` and
  `motor_power` without hanging the app; `.ses` parses; the empty-routing case is handled.

**Correctness risks:**
- **R1 — streaming vs sorting:** do **not** make `run_candidates` buffer-and-sort (would
  delay the first card by the full N-seed runtime). Rank in `cli.py` after the stream.
- **R2 — memory:** buffer only the lightweight projection, never the N serialized boards.
- **R3 — proxy distortion:** `sheet_spread_score` and `whitespace_connectivity` must
  exclude edge-pinned connectors and locked parts, or edge-heavy boards score
  misleadingly badly.
- **R4 — clamp divergence:** edit via the single shared helper; a per-phase test exercises
  seed→anneal→legalize with a non-zero keepout and asserts every component AABB stays
  ≥ keepout from the outline.
- **R5 — raw `channel_scale`:** must be stored on the Annealer (today only the product
  `self.channel` exists); forgetting it makes the gutter use the wrong magnitude.
- **R6 — channel/metric lockstep:** `_pair_penalty` and `pinch_fraction` must both use the
  shared `channel_width` helper.
- **R7 — FreeRouting determinism:** pin passes + `-de` seed so a finalist's routed-% is
  reproducible; the *ranking* is proxy-based and deterministic regardless.

---

## 9. Testing

Plain-Python, in-memory `Board`/`Component`/`Pad` construction (no `pcbnew`, no
`.kicad_pcb`), matching existing `tests/`.

- **`tests/test_candidate_ranking.py`** — `candidate_key`, `pre_rank`, `final_order` over
  mock candidate dicts: legal-beats-illegal, spread/pinch ordering, HPWL tiebreak, seed
  total-order, routed finalists float to top, `RANK=hpwl` fallback equals HPWL-min.
- **`tests/test_metrics_proxies.py`** — each proxy as `f(board) -> float`: known-geometry
  boards with asserted thresholds; edge/locked exclusion; single-sheet sentinel;
  empty-whitespace and no-pinch edge cases; `pinch_fraction` agreement with the channel
  helper.
- **`tests/test_edge_keepout.py`** — all three clamp paths with a non-zero keepout assert
  AABBs stay inside the inset outline; default-0 identity (G1).
- **`tests/test_multiseed.py`** — update field-shape assertion to a superset; assert the
  three proxy keys are present and numeric.
- **D4** — extend `test_engine.py`/`test_anneal.py`: determinism still holds; cross-block
  pairs get the wider target on a roomy board; dense board (`channel_scale=0`) gets the
  plain target.

Run: `python -m pytest tests/`.

---

## 10. Build order & follow-ups

**Build order:** D5 → D3 → D2 → D1 (ranking, then route-top-2) → D4.

**Deferred knobs (deliberately simple in v1, revisit on real-board evidence):**
- Folding `whitespace_connectivity` into `candidate_key` once boards show it helps.
- Epsilon-bucketing the lexicographic key (so a microscopic spread edge can't override a
  large HPWL gap) if strict lexicographic proves too aggressive on real boards.
- Routing more than the top-2 (K configurable) if wall-clock allows.

These belong to tuning after Phase 0 lands, not to this spec.
