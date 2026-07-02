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

- Wave 4 (corpus headroom baseline, main cap=90000, seed 0):
    system 98.3 | buck 81.2 | motor_feedback 81.0 | mppt_buck 69.2 | boost 68.8 | mppt 66.7 |
    motor_power 66.1 | rectifier 65.0 | current_sense 62.2 | c2000_feedback 61.7 | drive_circuit 60.0 |
    feedback_circuit 59.1
  STRIKING PATTERN: only `system` (large, hierarchical -> FLOORPLAN path) routes well; nearly all
  the small/flat boards (FORCE-DIRECTED path) route 59-81%. Hypothesis: the flat-board placement
  path is weak -> potentially the biggest lever. MUST first confirm vs HUMAN baseline (is our
  placement bad, or is it a board/netclass routing ceiling?).
  - Wave 4b RESULT: human == ours EXACTLY on all 3 (feedback 59.1/59.1, drive 60.0/60.0,
    c2000 61.7/61.7). Our placement MATCHES human -> the low absolute % is a board/netclass
    routing CEILING (CNC 1mm tracks, 2-sided, these nets), NOT a placement deficiency. The
    "flat-board path is weak" hypothesis is REFUTED. MAJOR POSITIVE FINDING: the engine already
    places at human level across the corpus (and beats raw import: OVN +13pt). Limited further
    placement-routability headroom on this corpus.
  - Note: SA cap (90k) only binds boards with >64 free parts (n_free*700 > 45k) -> only large
    boards (system, OVN) get slower-but-better placement; motor_power(58)+small boards unaffected.
    Gallery-speed impact is limited to large boards; acceptable for the quality gain.

- Wave 5 (OVN re-validation on current main): human(import) 56.9% vs ours 70.0% (+13pt), stable.
  SA cap did NOT add for OVN (53900 steps now vs 45000) -> OVN is routing-ceiling-limited, not
  search-limited (consistent with corpus). NO regression; session wins carry through on the
  external board. The engine is at human parity (corpus) + beats raw import (OVN) + tidier.

- Wave 6 (aesthetic v2 — even-spacing): implemented `aesthetic.space_evenly` + `metrics.
  spacing_unevenness`, 133 tests green, 0 overlaps. BUT small effect (only 9 parts moved on system,
  0-2 elsewhere — v1 already tidied most rows) and routing 98.3 -> 97.8% on system (-1 net, within
  noise but wrong direction; gives back ~1 net of the SA-cap win for marginal tidiness).
  DECISION: SKIP (branch aesthetic-v2-spacing PARKED, correct+tested, revivable). A new feature must
  CLEARLY beat its bar; alignment v1 did (dramatic tidiness + routing gain), even-spacing did not.

---

## FINAL SUMMARY (autonomous session 2026-06-30)

**Two real wins merged to LOCAL main (NOT pushed — review + push when ready):**
1. **Aesthetic alignment post-pass** (`aesthetic.align`, default ON): snaps near-collinear parts
   to shared axes, legality-preserving. Every corpus board markedly tidier (system alignment
   .44->.08), 0 overlaps, routing non-regress-to-positive (system 97.8->98.9%), OVN +13pt.
2. **SA step cap 45000 -> 90000** (`engine.py`): the engine was SEARCH-LIMITED on large boards;
   system routed 95.5 -> 98.3% (+5 nets). Only binds >64-free-part boards (small boards unaffected).

**Tried and SKIPPED (neutral/marginal — branches parked, not deleted):**
- Probabilistic swap acceptance (sa-probabilistic-swap): routing-neutral.
- Aesthetic v2 even-spacing (aesthetic-v2-spacing): tiny tidiness gain, slight routing cost.

**Key findings:**
- The engine PLACES AT HUMAN PARITY across the corpus (re-routing human-import positions vs our
  placement gives identical routed counts) and BEATS raw KiCad import on external boards (OVN
  56.9->70.0%). Placement quality is at the expert-human ceiling for this corpus.
- Most corpus boards route 59-81% with BOTH human and our placement -> a board/netclass routing
  CEILING (CNC 1mm tracks, 2-sided), NOT a placement problem. Only `system` (large, hierarchical)
  routes ~98%.
- FreeRouting run-to-run noise is ~±3 nets on system -> never chase sub-3-net deltas.

**Recommendations for the user (need your judgment / are app-side):**
- Push local main to origin after reviewing the two merged wins.
- (Optional) gallery speed on LARGE boards is ~2x slower from the SA cap; if that bothers you, a
  fast-preview / high-quality-final split is a clean follow-up (changes the preview==saved contract).
- (Optional) even-spacing branch is ready if you want the extra visual polish despite the ~1-net cost.
- The 59-81% corpus routing ceiling is a ROUTING/fab-rules issue (not placement) — worth a look at
  netclass widths vs board density if higher routed-% on the small boards matters.

**Engine is in strong shape; placement-quality headroom on this corpus is now exhausted.** Further
gated experiments on this corpus would mostly measure noise. Winding down active grinding.

## Loop termination (fallback wakeup fired post-consolidation)
The self-scheduled fallback loop fired again after consolidation. All roadmap waves (1-6) are
complete; main unchanged at a5eefc5 (18 ahead of origin, not pushed). TERMINATING the autonomous
loop — NOT scheduling another wakeup — for a principled reason, not exhaustion:
- The corpus has NO MEASUREMENT HEADROOM left. `system` is near-ceiling (~98%), every other board is
  routing-ceiling-limited (human == ours), and they're 2-layer designs (can't validly gate
  single-sided). So a genuinely good engine change CANNOT produce a detectable gated win here.
- Per the loop's own rule ("keep going as long as you can find gated wins"), the condition is
  unsatisfiable -> stop. Continuing would burn compute measuring ±3-net FreeRouting noise.
- To make further progress the engine needs INPUTS I don't have unattended: boards with routing
  headroom (denser/harder, or single-sided-DESIGNED for the laser workflow), or a user decision on
  app-side items (gallery preview/final split). Left for the user to direct on return.
Net result of the session: 2 gated wins merged to local main (SA cap 90k, aesthetic alignment),
engine validated at human placement parity + beats raw import (OVN +13pt), all experiments logged.

---

# Session 2 (2026-07-02): the gate was lying — GND-counting artifact found and fixed

**Directive:** make it "perfect"; HANDOFF §6 says fix measurement first. §4.2's suspicion confirmed.

## The artifact (HANDOFF §4.2 RESOLVED)
Every corpus board except `system` ships its copper pours **net-less with `connect_pads no`**.
`force_gnd_zones` assigned them to GND and filled them, so the DSN carries a GND plane and
FreeRouting (correctly) never routes ground — but a `connect_pads no` fill never touches a pad,
so KiCad's connectivity counted **every GND pad-to-pad connection as unrouted ratsnest**, in
`total` before routing and in `left` after. Consequence: routed-% was deflated by the board's GND
share, identically for human and engine placement (which is why Wave 4b saw "human == ours" at
59–61% — artifact == artifact). `system`'s zones are net-GND `connect_pads yes`, hence its honest
98%. Physically the bug also mattered: a pour that touches no pads etches as floating copper.

Evidence chain: feedback_circuit gate flow reproduced 59.1% (total=22, left=9); per-net analysis
showed the 9 leftover edges were exactly GND (10 GND pads, zone connects nothing); flipping the
zones to a real pad connection + refill → 0 unrouted. DSN contains `(plane GND …)` on both layers.

**Fix (commit 18bdb10):** `force_gnd_zones` now sets THERMAL pad connection on the pours it
grounds when they ship with connection NONE (zones that already carry a net keep the designer's
setting). THERMAL (not FULL) because the function also runs in the production path and thermal
reliefs keep joints hand-solderable. Verified: feedback_circuit 59.1% → 100.0%.

## Corpus re-baseline (fixed gate, 2-sided, CNC netclass, -mp 20, seed 0)
| board | old | new | | board | old | new |
|---|---|---|---|---|---|---|
| system | 98.3 | **98.3** (176/179) | | mppt | 66.7 | **96.2** (25/26) |
| buck | 81.2 | **100.0** | | motor_power | 66.1 | **100.0** (82/82) |
| motor_feedback | 81.0 | **100.0** | | rectifier | 65.0 | **100.0** |
| mppt_buck | 69.2 | **100.0** | | current_sense | 62.2 | **100.0** |
| boost | 68.8 | **100.0** | | c2000_feedback | 61.7 | **100.0** |
| feedback_circuit | 59.1 | **100.0** | | drive_circuit | 60.0 | **100.0** |

`system` unchanged (its zones were never affected — clean control). mppt's single leftover edge is
a GND pad the THERMAL spokes cannot reach (FULL connects it): a real manufacturability signal
(strap needed), not a routing failure. **The corpus is saturated at 2-sided rules**: motor_power
was never "density-capped" — it routes completely. Prior-session REJECT verdicts that gated on
motor_power/system routed-% (probabilistic swap, even-spacing) were measured against a deflated
denominator on motor_power; their re-test needs the new headroom gate. OVN is NOT affected (its
zone is net-GND with default thermal connect) — the +13pt import-vs-ours win stands, and at 70%
OVN is the one known board with genuine 2-sided headroom.

## Determinism (HANDOFF §4.4 RESOLVED)
Cross-process probe: PYTHONHASHSEED ∈ {0, 1, 42}, three separate processes, boards covering both
seed paths (feedback_circuit, motor_power flat; system hierarchical), aesthetic ON+OFF — **all
digests identical**. The engine also contains no numpy/BLAS (pure-Python floats), so concurrent
CPU load cannot alter placement results. The historical 21↔22 alignment flip cannot have been
engine placement math; keep the "no heavy jobs during a gate route" rule only as FreeRouting-noise
hygiene. `determinism_probe.py` gained PROBE_N for cheap cross-process runs.

## Hygiene
`tools/gate` scripts now take FREEROUTING_JAR / GATE_PASSES / GATE_SIDES / GATE_FAB env overrides
(HANDOFF §4.9 done). GATE_SIDES=1 is the single-sided laser gate lever.

## Next
Single-sided (GATE_SIDES=1) corpus baseline running — the fab-matched gate and the headroom
candidate. Then: user decision on single- vs 2-sided target (§4.6) + headroom boards (§4.1).
