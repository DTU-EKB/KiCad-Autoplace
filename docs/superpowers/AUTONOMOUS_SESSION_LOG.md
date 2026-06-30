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
