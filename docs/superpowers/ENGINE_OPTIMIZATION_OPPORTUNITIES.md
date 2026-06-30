# Engine Optimization Opportunities — Prioritized Analysis

**Generated:** 2026-06-30  
**Scope:** Pure read-only analysis of the full placement engine codebase.  
**Invariants respected:** `anneal._quality` untouched; power proximity = pad-to-pad hinges only; determinism (seed=0) preserved; legality maintained; pure modules (no pcbnew outside kicad_io).

---

## Methodology notes

- ±3-net FreeRouting run-to-run noise on the system board means only deltas ≥ 4 nets should be considered signal. All proposals below are expected to produce either larger structural changes or be measurable via proxy metrics.
- Weight-tuning is noise-limited and deliberately excluded (per brief). Every proposal here is algorithmic or structural.
- "Gate it" means: measure the proposed proxy, run FreeRouting on `system` + `motor_power`, require the gating metric to improve AND no regression.

---

## Opportunity 1 — SA Swap Move Should Accept Probabilistically, Not Greedily

**Title:** Probabilistic swap acceptance (match nudge/rotate acceptance policy)

**One-line:** Swaps are accepted greedily (`T=0`) while nudges/rotates use Metropolis; this asymmetry lets swaps dominate in the cold regime and locks the engine into a bad basin.

**Where:** `anneal.py:281-292`, specifically `_try_swap` and the call `self._accept(after - before, 0.0, ...)`.

**What change:** Pass the current temperature `T` instead of `0.0` to `_accept` in `_try_swap`. The change is one argument:
```python
# current
return self._accept(after - before, 0.0, lambda: self._revert2(a, b))
# proposed
return self._accept(after - before, T, lambda: self._revert2(a, b))
```
Optionally, reduce swap frequency (currently 20% of moves) or tune its cooling separately if full-T acceptance causes excessive global disruption in the cold phase.

**Why it could help:** Swaps permute two components' positions — a non-local, high-energy move that jumps over barriers. Accepting them greedily at T=0 means: (a) in the hot phase, bad swaps are accepted unconditionally (already fine — T is large anyway); (b) in the cold phase, greedy acceptance makes every late-stage swap permanent if it improves local cost, even slightly. This bypasses the Metropolis escape mechanism that nudges and rotates rely on. The result is that the SA search is pulled into whatever swap-basin it first lands in and cannot escape via probabilistic acceptance. Swaps are the only non-local escape move; if they are greedy, the algorithm is effectively a greedy local search for global topology once T drops. The classical SA literature is clear: all move types should share the same acceptance temperature for the Boltzmann distribution to be valid.

**Risk:** Low. The change is one argument; determinism is preserved (same RNG path). The only risk is that probabilistic swap acceptance during the hot phase slightly increases the chance of accepting very bad global permutations, temporarily hurting HPWL. The best-tracking (`_quality` sampling every `steps//300`) means the engine always returns the best-seen state, so a bad probabilistic swap that is not corrected still leaves the returned layout unharmed.  
**Invariant watch:** None — this is purely in `local_cost`/search, not `_quality`.

**How to gate it:** Measure `hpwl_mm` and `crossings` on `system` + `motor_power` across 3 seeds each (6 runs). Require no HPWL regression AND `crossings` delta ≥ 0 on system. Also check `pinch_fraction` proxy.

**Expected value:** **Likely-win.** Greedy swaps are a well-known SA anti-pattern. Even if the absolute HPWL gain is within noise, probabilistic swaps should reduce crossings (better global topology).

---

## Opportunity 2 — Floorplan Seed Uses Uniform Random Within Region; Replace with Connectivity-Weighted Seeding

**Title:** Connectivity-biased component seeding within floorplan regions

**One-line:** Parts are seeded uniformly at random within their block's region (line 111 `floorplan.py`); seeding near within-block net partners reduces the SA burden and gives a better initial HPWL.

**Where:** `floorplan.py:107-115`, the seeding loop inside `floorplan()`.

**What change:** Instead of placing each component at a uniformly random position in the region box (`rx0 + rw * (0.2 + 0.6 * rng.random())`), place it at a position biased toward the centroid of its net partners *within the same block*. Concretely:
1. Build a within-block net adjacency sum for each component (same adjacency structure as `blocks.py` but restricted to same-block peers).
2. Compute a target position as a weighted sum of current peer positions, with a fallback to the region center if no peers exist.
3. Jitter the target by a small fraction of the region size (e.g., `0.15 * rng.gauss(0, 1)`) for spread.
4. Clamp to the region box.

This is a one-time pre-computation before the seeding loop — no additional SA cost.

**Why it could help:** The floorplan seeds parts into their block region in a random order (the loop iterates `members[blk]` which is a list of refs in insertion order, effectively arbitrary). When SA starts, it must discover within-block connectivity from scratch, which wastes early SA budget on moves that should be unnecessary. A connectivity-biased seed means that closely-connected parts within a block start nearer each other, giving SA a better initial state and letting the early high-temperature phase explore globally rather than fixing obvious local wire-length. The `cohesion_scale=2.5` on hierarchical boards already pulls block members together during SA — a better seed means SA needs less effort to reach the same quality.

**Risk:** Low-medium. The seeding is pure Python and affects only the initial positions before SA; SA can recover from any poor seed. The risk is introducing a dependency loop (peer positions are computed while seeding, so earlier-seeded components influence later-seeded ones). Use two passes: first seed everything at region centers, then shift toward within-block net-weighted positions.  
**Invariant watch:** Determinism — must use `rng` exclusively, no dict iteration order dependencies. Iterate components in `sorted(members[blk])` order.

**How to gate it:** Compare `hpwl_mm` at the *start* of SA (post-seed, pre-anneal) against the current seed on `system` board. Expect lower initial HPWL. Then gate on FreeRouting `routed_%` and `crossings` proxy.

**Expected value:** **Likely-win** for hierarchical boards (floorplan path). Neutral for flat boards (force-directed path, unaffected). The system board (131 parts, hierarchical) is the primary target.

---

## Opportunity 3 — SA Nudge Amplitude Does Not Adapt to Board Density / Component Size

**Title:** Density- and size-adaptive nudge amplitude

**One-line:** Nudge amplitude is `max(1.0, T)` (line 249 `anneal.py`); on a dense 60mm board with 5mm components, a 1mm floor move is 20% of a component width and frequently creates overlaps that the overlap barrier immediately reverses, wasting steps.

**Where:** `anneal.py:249-260`, `_try_nudge`.

**What change:** Replace the fixed floor amplitude with one that scales with both the board and component:
```python
# current
amp = max(1.0, T)
# proposed  
board_scale = min(self.board.width, self.board.height) / 60.0   # relative to a 60mm ref
comp_scale = max(c.eff_w, c.eff_h)
amp = max(comp_scale * 0.5 * board_scale, T)
```
Alternatively, use the simpler: `amp = max(c.eff_w, T)` so the minimum displacement is half the component's own footprint (always meaningful). The cooling multiplies this down toward zero; only the floor matters.

**Why it could help:** The current floor of 1.0mm is a magic constant calibrated to a specific board size. On a 150mm × 100mm board, 1mm nudges are tiny (the part is already effectively stationary in the cold phase); on a 30mm × 30mm board, 1mm nudges move a part by several percent of board width — potentially past a partition boundary. Making the floor proportional to component size means cold-phase nudges always produce meaningful local adjustments rather than sub-pixel jitter. The ≥1.0mm floor was presumably added to avoid the annealer getting stuck, but it also means small boards (e.g., `motor_power`) receive proportionally large cold-phase perturbations that repeatedly create overlaps (the overlap barrier is 60×mm², so even a small overlap costs ~60 units — a massive rejection rate in the cold phase).

**Risk:** Low. Only changes the cold-phase exploration amplitude floor. The `_quality` sampling every `steps//300` snapshots means the best-seen state is always preserved. No invariant exposure.  
**Invariant watch:** None — search cost only.

**How to gate it:** Measure the **acceptance rate** (accepted_moves / total_moves) in the cold phase (last 20% of steps). A very low acceptance rate signals wasted computation. Expect the rate to improve. Gate on FreeRouting.

**Expected value:** **Coin-flip.** The effect is board-size dependent. On the system board (which is roomy), probably neutral. On dense boards like `motor_power`, could meaningfully improve SA convergence.

---

## Opportunity 4 — Block Centroids for Cohesion Are Resynced Only Every `steps//20` Steps; More Frequent Resync Helps Late-Stage SA

**Title:** Adaptive centroid resync frequency during SA

**One-line:** Block centroids are resynced every `max(200, steps//20)` steps (line 215 `anneal.py`); in the cold phase when parts are nearly settled, stale centroids cause the cohesion term to pull parts toward phantom positions.

**Where:** `anneal.py:215`, `resync_every = max(200, steps // 20)`, and line 235-238 where resync occurs.

**What change:** Use a two-phase resync schedule: resync more frequently as temperature drops (when components are nearly settled and the centroid is meaningful):
```python
# Instead of fixed resync_every, recompute it per resync event:
hot_resync = max(500, steps // 10)   # coarse in hot phase (centroids move a lot anyway)
cold_resync = max(50, steps // 100)  # fine in cold phase (small moves, centroid matters)
# in the loop:
resync_interval = hot_resync if T > 1.0 else cold_resync
if (it + 1) % resync_interval == 0:
    self.centroids = block_centroids(self.board)
```
`block_centroids` (blocks.py:104) is O(n) and cheap, so calling it 10× more often in the cold phase adds negligible runtime (the cold phase runs at the same step count but each step is fast since overlap-free).

**Why it could help:** The cohesion term (`self.cohesion * self._cohesion(c)`) pulls each component toward its block's current centroid. With `cohesion_scale=2.5` on hierarchical boards, this is a significant search force. When the centroid is stale by many component-widths (which it easily can be after 200+ nudge moves in the hot phase), the cohesion force points at a phantom target. In the cold phase, where components are nearly at their final positions, a resync every 50 steps means the cohesion term reflects the actual current arrangement and acts as a gentle refinement force rather than a noise source.

**Risk:** Low. `block_centroids` is pure, O(n), and read-only w.r.t. placement. More frequent resyncs only affect the search bias (cohesion is in `local_cost`, not `_quality`). Determinism is unaffected since the resync is tied to iteration count, not RNG.  
**Invariant watch:** None — cohesion is already in `local_cost` (search bias), not `_quality`.

**How to gate it:** Add an alignment proxy (`metrics.alignment_score`) measurement before and after this change. Expect improvement on hierarchical boards. Gate on FreeRouting non-regression.

**Expected value:** **Coin-flip.** Likely a small improvement on hierarchical boards with 2.5× cohesion. Neutral on flat boards.

---

## Opportunity 5 — `decoupling_pairs` Is Computed Once at Seed Positions and Held Fixed; Re-Pair Mid-Anneal

**Title:** Mid-anneal decap re-pairing (update pairs after SA settles)

**One-line:** `decoupling_pairs(board)` is called once in `Annealer.__init__` (line 92 `anneal.py`) and fixed for the entire run; as SA moves ICs and caps, the "nearest IC" pairing can become stale, locking in a suboptimal target for the decap hinge term.

**Where:** `anneal.py:92`, `self.decap = electrical.decoupling_pairs(board)`, and `_decap_penalty`.

**What change:** Re-compute `decoupling_pairs` at the same cadence as centroid resync (every `resync_every` steps, or at the mid-point of the anneal). Add a resync in the existing resync block:
```python
if (it + 1) % resync_every == 0:
    self.centroids = block_centroids(self.board)
    self.decap = electrical.decoupling_pairs(self.board)  # ADD
```
`decoupling_pairs` is O(n² in worst case) but the decap list is typically small (tens of caps on a large board), so the cost is negligible. Alternatively, re-pair only once at the midpoint of the anneal (`it == steps // 2`).

**Why it could help:** The decap hinge term (weight=3.0) was tuned to halve mean decap proximity on tested boards. But it is based on pairing from the seed positions, which can be far from final positions. If IC_A and IC_B are both near cap_C in the seed, the initial pairing might assign cap_C to IC_A; but after SA moves IC_B closer to cap_C, the pairing is now wrong and the hinge pulls cap_C toward IC_A's power pin (which is now farther away). Re-pairing mid-anneal ensures the hinge always pulls toward the actual nearest IC. The gain is bounded by the cases where pairings change — on boards where ICs move significantly during SA, this could materially improve decap proximity.

**Risk:** Low. `decoupling_pairs` is pure and deterministic. Re-pairing uses `sorted(comps)` iteration so order is deterministic. The decap term is in `local_cost` only (never `_quality`). The only subtle risk is that re-pairing mid-run could briefly increase local_cost if the new pair is farther, causing a surge of rejections — this is acceptable and self-correcting.  
**Invariant watch:** Power proximity is already in `local_cost` as a pad-to-pad hinge — this preserves that invariant while improving the hinge target accuracy.

**How to gate it:** Measure `metrics.decap_proximity(board)` before and after. Expect a measurable reduction (>5%) on boards with decap pairs. Gate on no FreeRouting regression.

**Expected value:** **Coin-flip.** On boards where ICs move substantially during SA (flat boards where force-directed seed is rough), re-pairing helps. On hierarchical boards where block cohesion keeps ICs near their seed region, the pairing is already stable and this is neutral.

---

## Opportunity 6 — Force-Directed Run Uses Fixed Spring/Repulsion Constants; Make Repulsion Stronger Early

**Title:** Temperature-scheduled force-directed repulsion (avoid premature overlap convergence)

**One-line:** `forcedirected.run` uses a fixed `k_repel=0.9` with step cooling (line 57 `forcedirected.py`); if repulsion is strong only early (before attractive springs dominate), parts spread cleanly before being pulled together, reducing SA's overlap-clearing burden.

**Where:** `forcedirected.py:57-129`, the `run` function, specifically `k_repel` and the force integration.

**What change:** Apply a repulsion multiplier that decays with `step` so repulsion dominates in the first third of FD iterations, then lets attraction shape the layout:
```python
# In the repulsion section:
repul_amp = k_repel * max(1.0, 3.0 * step)   # strong early, normal late
push = repul_amp
```
This scales repulsion by up to 3× when step=1.0 (hot), decaying to 0.9× (normal) as step→0.33, then normal below that. The step already cools via `step *= cooling` (line 127 `forcedirected.py`).

**Why it could help:** Currently, the FD run starts with all parts jittered into a grid (seed_positions), which means many are already overlapping or near-touching. Both attractive springs and repulsion fire simultaneously from step 1. If attraction is competitive early, parts can collapse toward net centroids before repulsion has cleared overlaps, creating dense clusters with unresolved overlaps that the legalize pass must fix by brute force (push_apart in `legalize.py:25-62`). Stronger early repulsion produces a better spread that attraction then shapes, analogous to a "simulated annealing" warm start for the FD phase. SA then starts from a cleaner topology rather than a half-legalized state.

**Risk:** Low-medium. If repulsion is too strong early, parts can be pushed to the board corners before attraction has any effect. The `_clamp_to_board` call on line 125 prevents exit from the outline. Test that `overlaps` count after FD (before SA) is lower with this change.  
**Invariant watch:** None — purely affects the seed quality passed to SA. Determinism preserved (no new RNG calls).

**How to gate it:** Measure `overlaps(board)` after `forcedirected.run` and before SA. Expect a reduction. Also measure `hpwl_mm` post-FD as a sanity check (should not blow up). Gate on final FreeRouting non-regression.

**Expected value:** **Coin-flip.** Benefit depends on how many overlaps the current FD seeding produces. If the current FD already produces clean spread (check `overlaps` count post-FD on system board), this is neutral. If it regularly produces 20+ overlaps, fixing them earlier is meaningful.

---

## Opportunity 7 — `_quality` Samples Every `steps//300` But the Best-Tracking Window Is Fixed; Add Periodic Restart From Best

**Title:** Periodic restart from best-seen state during SA

**One-line:** The annealer tracks and restores the best-seen layout at the end, but never restarts from it mid-run; adding periodic restart from best avoids prolonged exploration of an inferior basin while the cooling schedule is nearly frozen.

**Where:** `anneal.py:207-243`, the `run` loop. Current best-tracking: lines 219, 231-234, 240-243.

**What change:** Every `restart_every = max(steps // 5, 1000)` steps, if the current layout's quality is worse than `best_q` by more than a threshold (e.g., `0.05 * best_q`), restore the best snapshot and continue from there:
```python
restart_every = max(steps // 5, 1000)
if (it + 1) % restart_every == 0:
    q = self._quality()
    if q > best_q * 1.05:
        self._restore(best)   # pull back toward best if drifted badly
    elif q < best_q - 1e-9:
        best_q = q
        best = self._snapshot()
```
This is "iterated local search" embedded in SA: periodically snap back to the best seen, then let T continue cooling from where it is (not reset). The restart is conservative (only when >5% worse), so it does not negate exploration.

**Why it could help:** The SA loop runs for `steps` iterations with exponential cooling. After a series of accepted bad moves in the hot phase (building a worse-than-best layout), the annealer can spend many cold-phase steps optimizing within that bad basin. The best-tracking at `steps//300` intervals means the final restore is correct, but the cold-phase steps in a bad basin are wasted. Periodic restart from best redirects cold-phase computation to refining the actual best-seen state rather than a random perturbation of it. This is especially valuable when the SA step budget is tight (3500-step minimum, which is ~11 quality samples total).

**Risk:** Low-medium. Restoring the snapshot mid-run changes the RNG state trajectory (subsequent `self.rng.choice()` calls produce different values than they would have from the non-restored position). But determinism is preserved: given the same seed, the RNG sequence is identical and the restart condition (`q > best_q * 1.05`) is deterministic. The risk is over-exploitation (too frequent restarts kill exploration). The `1.05` threshold and `steps//5` interval should be tuned.  
**Invariant watch:** None. `_quality` is called read-only here (not modifying the selection metric, just sampling it for the restart decision).

**How to gate it:** Run `system` board with 3 seeds, compare `hpwl_mm` and `crossings` proxy. Check that the quality metric at run-end is lower (better) vs baseline. Gate on FreeRouting.

**Expected value:** **Coin-flip.** Classic SA theory says annealing from a good initial state is better than random restart; periodic return to best is a pragmatic hybrid. Benefit depends on how often the current SA drifts badly, which is hard to assess without instrumentation.

---

## Opportunity 8 — Legalize `push_apart` Can Undo Good SA Placement; Use a Gentler First Pass

**Title:** Legalization-preserving SA outcome via constrained push-apart

**One-line:** `legalize.push_apart` (200 iterations, axis-aligned pairwise push) moves components to resolve any remaining overlaps after SA, but it is topology-unaware and can scatter parts that SA carefully placed near their net partners.

**Where:** `legalize.py:25-62`, `push_apart`, and `legalize.py:65-75`, the `legalize` function.

**What change:** Before the full `push_apart`, run a short net-aware mini-anneal (10–20 steps, T=0.01) that resolves overlaps by prioritizing moves toward net centroids — effectively "legalize while respecting wirelength." Alternatively, in `push_apart`, when both components are free and the push direction is ambiguous (overlap is square, not clearly more horizontal or vertical), break ties by moving the component whose displacement *reduces* its net-HPWL contribution. Implementation:
```python
# In push_apart, when both free and ox ≈ oy (within 0.1mm):
if abs(ox - oy) < 0.1 and a.ref in free and b.ref in free:
    # break tie by net cost: move in the direction that reduces HPWL for the heavier component
    # (heuristic: move the component with more nets first along the axis that hurts less)
    ...
```
A simpler alternative: run a single greedy swap pass on the post-legalize board (swap pairs of free components if the swap reduces `local_cost` without creating new overlaps) — this is a legalization refinement pass.

**Why it could help:** The `push_apart` resolution is axis-aligned and greedy in component pair order (O(n²) pairs, iterated 200 times). The push direction is determined purely by which axis has more overlap (`if ox < oy: push x`), ignoring net connectivity entirely. On a dense board where SA has placed two connected components close together but with a tiny overlap, push_apart can push them apart along the net axis (making the wire longer) rather than along the perpendicular axis (keeping the wire short). After grid-snapping (which can re-introduce overlaps), a second 60-iteration `push_apart` pass runs, compounding any net-unfriendly moves. A legalization pass that is aware of the SA cost function — even minimally — could preserve more of the SA's HPWL optimization.

**Risk:** Medium. Legalization is safety-critical (the engine guarantees overlap-free output). Any change to the push logic must be tested for correctness (no residual overlaps). The existing `overlaps()` return from `legalize()` is the formal check. Introducing net awareness without breaking this guarantee requires careful implementation.  
**Invariant watch:** Legality (overlap-free, in-outline). The `overlaps_remaining` field in the engine report must stay 0 or improve. Do NOT add net-aware terms to `_quality` — this is a modification to the legalization procedure, not to SA selection.

**How to gate it:** Measure `hpwl_mm` immediately post-legalize (before aesthetic pass) and compare to hpwl post-SA (before legalize). The current gap between post-SA and post-legalize HPWL is the "legalization damage." Require this gap to shrink. Gate on `overlaps_remaining == 0` (hard) and FreeRouting non-regression.

**Expected value:** **Long-shot.** The legalization damage may already be small if SA produces nearly-legal layouts (which the overlap barrier term is designed to ensure). Without instrumentation of post-SA vs post-legalize HPWL, it is unclear whether this is worth the implementation complexity. Instrument first; implement only if the gap is >2%.

---

## Summary table (priority order)

| # | Title | Expected value | Risk |
|---|-------|---------------|------|
| 1 | Probabilistic swap acceptance | **Likely-win** | Low |
| 2 | Connectivity-biased floorplan seeding | **Likely-win** (hierarchical) | Low-med |
| 3 | Density-adaptive nudge amplitude | **Coin-flip** | Low |
| 4 | Adaptive centroid resync frequency | **Coin-flip** | Low |
| 5 | Mid-anneal decap re-pairing | **Coin-flip** | Low |
| 6 | FD stronger early repulsion | **Coin-flip** | Low-med |
| 7 | Periodic restart from best-seen | **Coin-flip** | Low-med |
| 8 | Legalization-preserving push_apart | **Long-shot** | Medium |

**Recommended build order:** 1 → 4 → 5 (all one-liners or small targeted changes) → 2 → 3 → 6 → 7 → 8 (decreasing confidence/increasing scope).

---

## What was deliberately excluded

- **Weight tuning** (HPWL, OVERLAP, EDGE, COHESION, CHANNEL, DECAP): noise-limited per the session log; all are already at validated operating points.
- **Multi-seed best-of-N on its own**: already implemented via `multiseed.run_candidates`; adding more seeds is a runtime cost, not an algorithmic opportunity.
- **Block ordering in floorplan**: the `_order_chain` greedy chain in `floorplan.py:45-57` was noted as a candidate, but "flow anchor" block ordering was already tried and rejected (neutral per memory notes); re-proposing the same idea in a different form is unlikely to add value.
- **Congestion field cell size**: `CELL_MM=5.0` is shared between `congestion.py` and `metrics.py` (already the single source of truth); retuning it is weight-tuning.
- **FreeRouting passes (`-mp`)**: pipeline parameter, not engine optimization.
