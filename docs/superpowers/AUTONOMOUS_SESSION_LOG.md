# Autonomous optimization session log

**Started:** 2026-06-30 (user away; granted total creative freedom to optimize the autoplacer).

## Operating rules
1. **Gated:** no change lands unless `python -m pytest tests/ -q` stays green AND a FreeRouting
   non-regression holds on the corpus (system + motor_power minimum) AND the relevant quality
   metric improves. Most ideas are neutral on this corpus — keep only measured wins.
2. **Local main only.** Validated wins are committed/merged to LOCAL `main`. NO push to the shared
   `DTU-EKB` origin while unsupervised. User reviews git history and pushes when back.
3. **Invariants:** never modify `anneal._quality`; power proximity = pad-to-pad hinges in
   `local_cost`, never net weights; determinism preserved (seed=0 reproducible in-process).
4. **Transparency:** every experiment logged below with before/after numbers (wins AND rejects).

## Baselines (this session, KiCad python, -mp 20, sides=2)
- main @ `90b4f76` (DECAP=3.0, aesthetic OFF): system 97.8% (175/179), motor_power 65.3% (81/124).
- aesthetic branch @ `aa5ded9` (pre-fix): system 98.9% (177/179), motor_power 66.1% (82/124);
  alignment_score system .44->.08, all corpus boards markedly tidier, 0 overlaps.
- OVN (external 2-layer board, raw KiCad import vs ours, OVN netclass): 56.9% -> 70.0% routed.

## Roadmap (priority order; adopt only if gated win)
1. Land aesthetic alignment — resolve the `_clusters` refactor (aa5ded9 score .08 / 94 moves vs
   post-fix 47da24f score .18 / 103 moves; metric definition changed between them, so re-gate by
   ROUTING to pick the better, then merge to local main).
2. Systematic weight optimization — cheap proxy sweep over `_Weights` (HPWL/OVERLAP/EDGE/COHESION/
   CHANNEL/CONG_K/DECAP) + ALIGN_TOL, FreeRouting-gate top candidates, adopt better operating point.
3. Aesthetic v2 — even-spacing within aligned rows + decap-to-IC offset snap (gated).
4. Single-sided CNC routability (the tool's core use case) — placement tweaks that help one-layer routing.
5. Broaden corpus + OVN validation; polish; optional app aesthetic toggle UI.

## Wave log
(appended per wave: what was tried, gate result, decision)

- Wave 0 (setup): rules + roadmap recorded. Waiting on OVN CNC route to free FreeRouting before
  starting gated waves (no concurrent routing — contention risks nondeterminism + slows both).

- OVN external-board test (2-layer, 160 connections):
  - OVN-own netclass: raw import 56.9% (91/160) vs ours 70.0% (112/160); route time 368s -> 174s.
  - CNC netclass (clearance 0.85 / track 1.0, applied=True): raw import 56.9% vs ours 70.0%
    (identical to within rounding) -> the +13pt win is robust under CNC fab rules. 70% is a floor
    (board is denser/2-layer vs the single-sided target). CONCLUSION: the placer materially helps
    a never-seen external board, faster, under CNC rules.

- Wave 1 (land aesthetic alignment) — DONE, MERGED to local main:
  - Clean back-to-back A/B (no concurrent load): post-fix HEAD system 98.9% (177/179) /
    motor_power 66.1%; pre-fix aa5ded9 system 95.5% (171/179) / motor_power 66.1%.
  - Post-fix (DRY _clusters) routes >= pre-fix -> NOT a regression. The earlier alignment_score
    0.08->0.18 was a metric-DEFINITION change, not worse alignment; routing ground truth confirms.
  - METHODOLOGY NOTE: routing has ~±3-net run-to-run noise on system (aa5ded9 = 98.9% earlier,
    95.5% now). Treat sub-3-net deltas as non-regression; lean on proxies + direction + multiple
    boards, never chase ±1-2 net "wins".
  - Feature net effect vs main baseline (aesthetic OFF 97.8%): ON 98.9% + every corpus board
    much tidier (system alignment .44->.08) + 0 overlaps + OVN +13pt. DECISION: MERGE.

- Engine scout (read-only analysis -> docs/superpowers/ENGINE_OPTIMIZATION_OPPORTUNITIES.md):
  ranked 8 algorithmic opportunities. Top "likely-wins": (1) probabilistic swap acceptance
  (swaps accepted greedily at T=0 while nudge/rotate use Metropolis), (2) connectivity-biased
  floorplan seeding. Coin-flips: density-adaptive nudge, adaptive centroid resync, mid-anneal
  decap re-pair, FD early repulsion, restart-from-best. Long-shot: legalize-preserving push_apart.

- Wave 2 (SA effort) — STRONG SIGNAL (system, clean run): routed-% vs sa_steps:
    0.5x (22500) 89.4% | 1.0x (45000=current cap) 95.5% | 2.0x (90000) 98.3%.
  Monotonic, +5-11 nets per doubling (>> ±3 noise). The engine is SEARCH-LIMITED: the 45000
  cap in engine.py (`min(45000, n_free*700)`) starves large boards. -> raise the cap.
  - Confirmation: motor_power (free=58, default 40600 < cap; NOT cap-limited) flat 66.1% across
    0.5/1/2x -> converged, more SA neither helps nor hurts. system placement at 90000 = 42s (OK).
  - DECISION: raised cap 45000 -> 90000 (engine.py). Default-path re-route: system 98.3% (176/179,
    +5 nets vs 95.5%), motor_power 66.1% (flat). MERGED to local main (71c1eed). 112 tests green.
  - Trade-off logged: ~2x placement time on large boards (system 20->42s) -> slower multi-seed
    gallery on big boards. A fast-preview/quality-final split is a possible future refinement.

- Wave 3 (probabilistic swap, branch sa-probabilistic-swap 16fb237): swaps were accepted greedily
  (T=0) while nudge/rotate use Metropolis. Hypothesis: most interesting for motor_power, STUCK at
  66.1% regardless of SA effort (a basin only a non-local escape move can leave). Gating multi-seed
  (system + motor_power, seeds 0/1/2) on top of the raised cap.
  - RESULT: NEUTRAL. motor_power mean 65.8% (swap) == 65.8% (main); system 98.9% (swap) vs 98.3%
    (main) = +1 net, within noise. Hypothesis NOT supported: motor_power is density-capped, not
    basin-trapped. DECISION: SKIP (do not merge); branch sa-probabilistic-swap PARKED (clean
    correctness change; revisit if a headroom board later shows benefit).

- KEY STRATEGIC FINDING: the two gate boards are now at their limits — system ~98.3-98.9%
  (near ceiling, ~2-3 unroutable nets), motor_power ~65.8% (density-capped, flat to SA effort
  and swap policy). Neither can SHOW further engine improvement. To detect future wins I must
  gate on corpus boards that have routing HEADROOM.

- Wave 4 (corpus headroom baseline): route all 12 canonical corpus boards at seed 0 on current
  main (cap=90000) to find which have headroom (<~90%) so later experiments are measured where
  signal is visible. [in progress]
