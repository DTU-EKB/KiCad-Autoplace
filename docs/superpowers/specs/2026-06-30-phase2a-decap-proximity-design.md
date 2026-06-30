# Phase 2A — Decoupling-cap → IC-power-pin proximity

**Date:** 2026-06-30
**Status:** Design approved; ready for implementation plan.
**Roadmap context:** First increment of Phase 2 (electrical-aware placement). Phase 2 ships
**one term at a time, each gated on a real FreeRouting non-regression** on `system` +
`motor_power`. This is the highest-value term: pull each decoupling cap to its IC's power pin —
"the single most recognizable human move," and one the engine *cannot make today* because a
decap sits only on power + GND (both excluded from the annealer's `net_members`), so it has
**zero attractive force** and overlap-spreading scatters it.

---

## 1. Goal

Make decoupling caps hug the IC power pin they bypass — both as a placement bias (the annealer
visits hugged layouts) and as a **guaranteed** outcome of the multi-seed gallery (the
recommended candidate is a hugging one). Built on Phase 1's `classify_net` + enriched fields.

## 2. The Phase 2 framework (this increment instantiates it)

Every Phase 2 electrical term has the same shape:
1. **A pure detector** (new `electrical.py`) consuming `nets.classify_net` + `value`/`fpid`/
   `pin_type`, returning a deterministic structural pairing computed **once** in
   `Annealer.__init__` (stable across the anneal).
2. **A hinge penalty added to `anneal.local_cost` only — never `_quality`** — as a
   per-component term (mirroring `_cohesion`). Pad-to-pad distance, `max(0, dist − TARGET)`.
   **Never a net weight** (power nets are skipped by `net_members`, so weighting them is a
   silent no-op — this is exactly why it must be pad-to-pad).
3. **Surfaced in the gallery** as a quality metric folded into the multiseed ranking, so
   selection prefers the term's outcome without touching `_quality`.
4. **Gated before merge** by a FreeRouting non-regression (routed-% must not drop vs baseline)
   on `system` + `motor_power`, plus the term's own metric improving.

## 3. Scope — five pieces

| # | Piece |
|---|---|
| **A1** | `electrical.decoupling_pairs(board)` — pure detector. |
| **A2** | Decap proximity term in `anneal.local_cost` (precomputed pairing in `Annealer.__init__`). |
| **A3** | `metrics.decap_proximity(board)` — pure quality metric. |
| **A4** | Fold `decap_proximity` into the multiseed gallery ranking + surface it on the card. |
| **A5** | Phase-1 carry-over cleanup: tighten `nets._SENSE_RE`; make the detector the first `classify_net` consumer and document the `POWER_HINTS` vs `classify_net` relationship. |

### Out of scope (later Phase 2 increments)
- Crystal/oscillator hug, series/gate-R-at-driver (2B); connector orientation (2C); per-pair
  clearance + tall-part shadow (2D); block-order flow anchor (2E).
- Unifying `metrics._is_power`/`POWER_HINTS` with `classify_net` into one power oracle — A5 only
  *documents* the relationship; a full reconciliation is deferred (it touches HPWL exclusion,
  which is load-bearing and separately gated).

---

## 4. Guiding invariants

1. **`anneal._quality` is never modified.** The decap term lives only in `local_cost` (search
   bias). Selection still ranks visited layouts by HPWL + overlap; the *gallery* ranking (A4) is
   where decap quality influences the chosen candidate.
2. **Pad-to-pad, never net weight.** The term is a geometric distance between two pads, added in
   `local_cost`. No change to `net_members`, no re-inclusion of power nets in HPWL.
3. **Determinism.** The detector is a deterministic structural query; pairing is computed once on
   the seed positions with a `ref`-sorted tiebreak and fixed for the anneal.
4. **No default-behavior regression on boards without decaps.** A board with no detected decap
   pairs gets an empty pairing → the term contributes 0 → placement unchanged.
5. **FreeRouting-gated.** Merge only if routed-% on `system` + `motor_power` does not regress vs
   the recomputed baseline, and `decap_proximity` improves on boards that have decaps.

---

## 5. Current state (verified against `main`)

- `Annealer.__init__` (`anneal.py:48-80`) precomputes `comp_nets`, `net_members` (signal nets
  only — `_is_power` skipped, `anneal.py:75`), `centroids`, plus the Phase-0 `channel_scale`/
  `channel_mm`/`gutter`. `_Weights` (`anneal.py:31-37`): HPWL 1.0, OVERLAP 60, EDGE 0.6,
  COHESION 0.35, CHANNEL 4.0, CONG_K 3.0.
- `local_cost(subset)` (`anneal.py:149-…`) sums HPWL over touched nets + `_pair_penalty` vs all
  others + per-component `EDGE`(connectors) + `cohesion`. **A decap has no entry in `comp_nets`**
  (its nets are power/GND, excluded), so it contributes no HPWL and feels only overlap/channel/
  cohesion — it is a free-floating ghost. The new term is added in the per-component loop.
- `nets.classify_net(board, net)` (Phase 1) → `GROUND|POWER|SENSE|SIGNAL|NC`; `_SENSE_RE`
  currently substring-matches `FB`/`ADC`/`FEEDBACK` (over-broad).
- `ranking.candidate_key` (`ranking.py`) = `(overlaps, sheet_spread, pinch_fraction, hpwl_mm,
  seed)`; `multiseed.run_candidates` builds candidate dicts; the gallery card shows the proxies.

---

## 6. Design

### A1 — `electrical.decoupling_pairs(board) -> dict` (new `electrical.py`, pure)

A **decoupling cap** = a component with exactly 2 pads whose two nets classify (via
`classify_net`) as one `POWER` and one `GROUND`. For each decap, find its **target IC**: the
component with **> 2 pads** that has a pad on the **same POWER rail net**, nearest by current pad
position (Euclidean, cap's rail pad → candidate's rail pad); ties broken by `ref` (deterministic).
If no such multi-pad part exists on that rail, the cap is skipped (no target).

Returns `{cap_ref: (cap_rail_pad_idx, ic_ref, ic_rail_pad_idx)}`. Pure; no RNG; no `pcbnew`.
Computed once (on seed positions) so the pairing is stable for the whole anneal.

### A2 — Decap term in `anneal.local_cost`

- `Annealer.__init__`: add `self.decap = electrical.decoupling_pairs(board)` (once). Add
  `_Weights.DECAP` and a module constant `DECAP_TARGET_MM ≈ 3.0`.
- New `Annealer._decap_penalty(c)`: if `c.ref` in `self.decap`, look up
  `(cap_pad_idx, ic_ref, ic_pad_idx)`, compute the live world distance
  `d = dist(c.pad_world(c.pads[cap_pad_idx]), ic.pad_world(ic.pads[ic_pad_idx]))`, return
  `max(0.0, d − DECAP_TARGET_MM)`; else `0.0`.
- In `local_cost`'s per-component loop: `cost += self.decap_weight * self._decap_penalty(c)`
  (where `self.decap_weight = _Weights.DECAP`).

**The cap is the only thing pulled** (per-component term, like `_cohesion`): when the IC moves,
caps targeting it are not re-penalized in the IC's move-delta — an accepted approximation (the
existing `_cohesion` has the same asymmetry, and the cap follows on its own moves). Because a
decap otherwise feels *no* attractive force, even a modest `DECAP` weight dominates its motion;
starting value `DECAP ≈ 1.5`, tuned against A3 + the FreeRouting gate.

**Why it survives selection:** it doesn't have to, in the single-seed `_quality` — but the
gallery (A4) ranks candidates partly by `decap_proximity`, so the recommended candidate is a
hugging one. (Spelled out in the framework; this is the user-requested guarantee.)

### A3 — `metrics.decap_proximity(board) -> float`

Mean cap→IC-pin distance over `electrical.decoupling_pairs(board)` (pad-to-pad, same pads the
term uses). Lower is better. Returns `0.0` when there are no decap pairs (so decap-free boards
are neutral in ranking). Pure, deterministic.

### A4 — Gallery ranking + card

- `multiseed.run_candidates`: add `decap_proximity` to each candidate dict (call
  `metrics.decap_proximity(board)`).
- `ranking.candidate_key`: insert `decap_proximity` (rounded to `0.5 mm` buckets so only
  meaningful differences matter) **between `pinch_fraction` and `hpwl_mm`** →
  `(overlaps, sheet_spread, pinch_fraction, round(decap_proximity*2)/2, hpwl_mm, seed)`. So decap
  closeness is a tiebreak above HPWL but below the routability proxies (it must not override
  routability). Boards without decaps score `0.0` → no effect on their ordering.
- Gallery card (`renderer.js`): add `decap <d> mm` to the proxy row. `cli` buffer projection +
  the lightweight `ranking` keys gain `decap_proximity`.

### A5 — Phase-1 carry-over

- **Tighten `nets._SENSE_RE`** to require token boundaries, e.g. match `FB`/`ADC`/`SENSE`/`VREF`/
  `ISNS`/`ISEN`/`VSEN`/`FEEDBACK` only at a name-segment boundary (`(^|[_/])TOKEN([_/0-9]|$)`),
  so `USB_D_FB`, `CADC` no longer mis-classify as SENSE. Add regression tests for the
  previously-misclassified names.
- **Detector is the first `classify_net` consumer**: `decoupling_pairs` uses `classify_net` for
  POWER/GROUND, not `_is_power`. Add a one-paragraph note in `electrical.py`/`metrics.py`
  documenting that `metrics._is_power`/`POWER_HINTS` (HPWL exclusion) and `nets.classify_net`
  (role tagging) are two intentionally-separate power heuristics, with full unification deferred.

---

## 7. Validation

**Pure unit tests (plain `pytest`):**
- `tests/test_electrical.py`: `decoupling_pairs` on synthetic boards — a 2-pad cap on
  `{+5V, GND}` pairs to the nearest >2-pad part sharing `+5V`; nearest-of-two tie broken by ref;
  a cap whose rail has no IC is skipped; a 3-pad part is not a decap; a 2-pad cap on
  `{signal, signal}` is not a decap.
- `tests/test_metrics_proxies.py` (extend): `decap_proximity` returns mean distance; `0.0` for a
  decap-free board.
- `tests/test_candidate_ranking.py` (extend): a candidate with closer decaps outranks an
  otherwise-equal one; decap-free candidates are unaffected (score 0.0).
- `tests/test_engine.py`/`test_anneal.py`: with a decap pair present, the annealer ends with the
  cap closer to its IC pin than a run with `DECAP` weight 0 (term has effect); a board with no
  decaps is byte-identical to `DECAP`-off (invariant #4); determinism holds.
- `tests/test_nets.py`: the tightened `_SENSE_RE` (A5) — `USB_D_FB`→SIGNAL, `CADC`→SIGNAL, while
  `FB`, `ADC_V1`, `ISENSE` still →SENSE.

**FreeRouting gate (KiCad python, the real test):**
- **Baseline (current `main`, recorded 2026-06-30):** `system` **95.0%** (170/179),
  `motor_power` **66.1%** (82/124), 20 passes, each board's own netclass, double-sided. Measured
  by `scratchpad/route_baseline.py` (copies the board, places with the current engine, **strips
  the board's pre-existing routed tracks**, then routes — the corpus boards ship fully routed, so
  *not* stripping leaves ~800 stale traces that crater FreeRouting to ~30%; this was a harness bug,
  not an engine regression).
- After A2: re-place + route the same two boards with the identical harness; **require routed-% ≥
  baseline − ~2% noise** (no regression: `system` ≥ ~93%, `motor_power` ≥ ~64%), and
  **`decap_proximity` strictly lower** on boards that have decaps. Record both numbers. If routing
  regresses, tune `DECAP`/`DECAP_TARGET_MM` or stop and reassess — do not merge a routability
  regression.

---

## 8. Risks

- **R1 — selection vs search.** The term biases search; the gallery ranking (A4) provides the
  selection guarantee. Documented; the user explicitly chose to add A4.
- **R2 — pairing wrong IC.** "Nearest multi-pad part on the rail" is a heuristic; a cap could pair
  to the wrong IC on a board with several ICs on one rail. Low harm (still pulls toward *a* user
  of the rail); the `decap_proximity` metric + FreeRouting gate catch gross damage. Deterministic.
- **R3 — DECAP weight fighting cohesion/spread.** Too strong → caps clump and fight block spread
  (could hurt routing). The FreeRouting gate is exactly the guard; tune `DECAP` down if routed-%
  drops.
- **R4 — over-tightening `_SENSE_RE`** could now MISS a real sense net. Mitigated by keeping the
  token list and only adding boundaries; tests cover both directions.

## 9. Build order

A5 `_SENSE_RE` tighten (pure, isolated) → A1 detector + tests → A3 metric + tests → A2 anneal term
+ tests → A4 ranking/gallery + tests → **FreeRouting gate on `system` + `motor_power`**. The gate
is the final step before merge.
