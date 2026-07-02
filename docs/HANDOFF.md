# KiCad-Autoplace — Engineering Handoff

**Audience:** a fresh Claude Code (or human) session tasked with making this auto-placer "perfect."
**Date:** 2026-07-02. **Author:** the prior autonomous optimization session.
**Read this first, then `docs/superpowers/AUTONOMOUS_SESSION_LOG.md` (blow-by-blow) and
`docs/superpowers/ENGINE_OPTIMIZATION_OPPORTUNITIES.md` (ranked ideas).**

---

## 0. TL;DR — where things stand

- The placement engine is **good**: it places at **expert-human parity** across the DTU corpus
  (re-routing the human footprint positions vs our placement yields *identical* routed counts) and
  **beats raw KiCad import** on a fresh external board (OVN: 56.9% → 70.0% routed, +13 pts).
- Two gated wins landed this session: **SA-effort cap 45k→90k** (`system` 95.5%→98.3%) and an
  **aesthetic alignment post-pass** (every board tidier, 0 overlaps).
- **The main blocker to further progress is MEASUREMENT, not the engine.** The test corpus has no
  routing headroom left to reveal improvements (see §4.1). Fixing that is the #1 priority — without
  it, you cannot tell a good change from noise.
- **Suspicion worth checking first (§4.2):** most small corpus boards route only 59–81% under BOTH
  human and our placement. That *might* be a real board/netclass ceiling — or a **gate harness
  artifact** (e.g. GND ratsnest miscount). If it's an artifact, every routed-% number in the logs is
  wrong and must be re-measured. **Verify this before trusting anything.**

> **2026-07-02 UPDATE (session 2):** §4.2 CONFIRMED as a gate artifact and FIXED (commit
> `18bdb10`); §4.4 determinism VERIFIED cross-process; §4.9 gate paths parameterized. With the
> fixed gate the corpus routes **100% on every small board** (mppt 96.2%: one GND pad thermal
> spokes can't reach), `system` unchanged at 98.3%. All pre-fix routed-% in this doc and the
> session-1 log are deflated on boards with net-less pours — do not compare against them. See
> "Session 2" in `docs/superpowers/AUTONOMOUS_SESSION_LOG.md`.

---

## 1. What the project is

- **KiCad-Autoplace**: an Electron desktop app + a pure-Python PCB auto-placement engine, for DTU
  Ballerup students. The user drives it via the **app** (not the plugin). Repo: `DTU-EKB/KiCad-Autoplace`.
- **Engine** lives in `plugin/plugins/autoplace/` (pure Python, unit-tested on plain `python`).
  `kicad_io.py` is the ONLY module that imports `pcbnew`.
- **Goal:** "senior-engineer-quality" placements — deliberate, electrically sound, routable, visually
  clean — not just HPWL-minimal-but-scattered.

## 2. Architecture you must understand before touching anything

**Pipeline** (`engine.place`, `engine.py`):
`detect_blocks` → seed (**floorplan** for hierarchical/≥2-sheet boards, else **force-directed**) →
**simulated annealing** refine → **legalize** (push-apart + grid-snap + clamp) → **aesthetic** post-pass.

**The two-judge SA split — THE load-bearing invariant (BUILD_SPEC.md:368-379):**
- `anneal._quality` = **SELECTION** metric = `HPWL(signal) + overlap barrier` **only**. This decides
  which visited layout is kept. **NEVER add soft terms to `_quality`.** Doing so made the engine
  discard low-HPWL layouts and return cohesion/overlap-optimal but wirelength-bad ones.
- `anneal.local_cost` = **SEARCH** bias = HPWL + overlap + channel + cohesion + edge + **decap**.
  All electrical/aesthetic shaping goes here, as **hinge terms with a printed explanation**.

**Other invariants (violating these has burned prior sessions):**
1. Power-aware proximity = **pad-to-pad geometric hinge terms**, never net weights. Power nets are
   excluded from `net_members`, so weighting a power net is a **silent no-op**. Never re-enable global
   power HPWL (collapses the board).
2. Determinism: seed=0 must be reproducible. Use `self.rng` only; sort before iterating sets/dicts
   where the RESULT depends on order.
3. Every new term ships **one at a time, gated on a FreeRouting non-regression** (§3).
4. Hard vetoes (creepage, tall-shadow) belong in a **legality gate**, not as weighted cost addends.
5. Aesthetic post-pass moves must be **legality-preserving** (overlap-guarded + in-bounds) and small.

**Key modules:** `anneal.py` (SA), `floorplan.py` (region seed for hierarchical), `forcedirected.py`
(seed for flat), `blocks.py` (`detect_blocks` → sheet-based or net-cluster block ids), `legalize.py`,
`aesthetic.py` (alignment + even-spacing), `metrics.py` (proxies + validation metrics),
`ranking.py`/`multiseed.py` (gallery candidate ranking), `edge.py` (connector edge placement +
orientation), `electrical.py` (`decoupling_pairs`), `footprints.py` (`height_mm`), `nets.py`
(`classify_net`), `congestion.py`, `geom.py` (`clamp_center`), `routing.py` (FreeRouting bridge),
`fabrication.py` (fab profiles), `model.py` (Board/Component/Pad; no pcbnew).

## 3. How to run + gate (reproduce the results)

- **KiCad-10 python** (has pcbnew): `C:\Program Files\KiCad\10.0\bin\python.exe`. **Plain `python`**
  for pytest (pcbnew NOT importable there).
- **FreeRouting:** `%USERPROFILE%\.freerouting\freerouting-1.9.0.jar` + Java 21.
- **Board corpus:** `C:\Users\Mads2\DTU\4. Semester\Electrical Energy Systems\team\hardware\kicad`
  (the DTU team boards). External test board: `C:\Users\Mads2\OvnProjekt\OVN\OVN.kicad_pcb`.
- **Tests:** `cd <repo> && python -m pytest tests/ -q` (currently **112 passing**).
- **Gate harness (committed in `tools/gate/`, see its README):** the workhorse is
  `route_baseline.py` — copies a board to scratch, places it with the engine, **strips existing
  tracks** (⚠️ the corpus boards ship FULLY ROUTED — you MUST strip or you route around ~800 stale
  traces and get garbage), routes with FreeRouting, reports routed-%. Run under KiCad python.
  - Example: `"<kicad10-python>" tools/gate/route_baseline.py <scratchdir> <board1.kicad_pcb> ...`
- **Gate rule:** a change ships only if `pytest` stays green AND FreeRouting routed-% does not regress
  on `system` + `motor_power` (the two canonical gate boards) AND the term's own proxy metric improves.

## 4. Limitations & problems (the important part)

### 4.1 No measurement headroom on the corpus (THE blocker)
`system` is near-ceiling (~98%). Every other board is routing-ceiling-limited (§4.2). They are all
**2-layer designs**, so routing them single-sided (the actual laser fab) is invalid. Consequence: a
genuinely better engine **cannot produce a detectable gated win** on these boards. This is why the
prior session stopped — not because the engine is perfect, but because the corpus can't *show*
improvement. **To make real progress you need boards with routing headroom** (denser/harder, or
single-sided-*designed* boards for the laser flow). This is the first thing to fix.

### 4.2 [RESOLVED 2026-07-02: harness bug] Small boards route 59–81% under BOTH human and our placement — real ceiling or harness bug?
> It was the harness: net-less `connect_pads no` pours got grounded+filled but never touched a pad,
> so every GND connection counted as permanently unrouted (FreeRouting skips GND — it sees a
> plane). Fixed in `force_gnd_zones` (THERMAL pad connection on grounded pours). Corpus re-baseline:
> all small boards 100% (mppt 96.2%), `system` 98.3% unchanged.
Corpus baseline (our placement, seed 0, CNC netclass, 2-sided, -mp 20):
`system 98.3 | buck 81.2 | motor_feedback 81.0 | mppt_buck 69.2 | boost 68.8 | mppt 66.7 |
motor_power 66.1 | rectifier 65.0 | current_sense 62.2 | c2000_feedback 61.7 | drive_circuit 60.0 |
feedback_circuit 59.1`. On the 3 worst, human-import positions route **identically** to ours →
placement is not the cause. BUT a 22-net board routing 59% is *suspicious*. **Before trusting any
number, confirm this is a genuine board/netclass limit and not a gate artifact** (e.g. GND poured as
a zone but counted as unrouted ratsnest, NPTH/thermal pads, or a netclass width mismatch). Inspect the
unrouted nets on one small board (`.routed.kicad_pcb` + `unrouted_count` in `kicad_io`). If it's an
artifact, **re-baseline everything** — the human-parity conclusion could shift.

### 4.3 FreeRouting run-to-run noise ≈ ±3 nets on `system`
Same placement re-routed varies by ~3 nets. Multi-seed averaging (see `tools/gate/swap_gate.py`)
mitigates but is expensive. **Never chase sub-3-net deltas.** Several "neutral" verdicts this session
rest on this — a lower-noise or larger-N methodology would sharpen them.

### 4.4 [RESOLVED 2026-07-02: verified deterministic] Determinism under concurrent load — not fully root-caused
> Cross-process probe (PYTHONHASHSEED 0/1/42, both seed paths, aesthetic ON+OFF): all digests
> identical. Engine has no numpy/BLAS — pure-Python floats — so CPU load cannot change placement.
> Keep the no-concurrent-routing rule only as FreeRouting-noise hygiene.
Early on, `motor_power` alignment counts flipped 21↔22 across separate processes under concurrent CPU
load (multiple FreeRouting/subagents running). An in-process 5× probe (`tools/gate/determinism_probe.py`)
showed the engine IS deterministic in-process and `PYTHONHASHSEED`-independent. The cross-process flip
was most plausibly BLAS-threading nondeterminism under contention, but was NOT definitively proven.
**Verify determinism rigorously** (fix `PYTHONHASHSEED`, check for threaded float reductions) — it
underpins gate validity. Practical rule used: never run CPU-heavy placement while a gate route runs.

### 4.5 Gallery speed regression from the SA-cap raise
Raising the SA cap to 90k ~doubles placement time on **large** boards (`system` ~20s→42s). It only
affects boards with >64 free parts, so small boards + the common case are unchanged. But the
multi-seed gallery on a big board is now ~2× slower. **Fix candidate:** fast-preview (lower steps) +
high-quality-final (90k) split in `multiseed`/`cli.py`. **Caveat:** this breaks the current
"preview thumbnail == saved board" determinism contract (`cmd_place_multi`). Needs a product decision.

### 4.6 Single-sided vs 2-sided target ambiguity (fundamental)
The tool's fab profiles (`fabrication.py`: `laser`, `cnc`) and the `kicad-laser-pcb` skill target
**single-sided** etched boards. But the test corpus and OVN are **2-layer designs**, and all gating
used 2-sided routing. It's unclear which the engine should optimize for. If single-sided is the real
target, you need single-sided-designed boards and a single-sided gate (`routing.route_once(..., sides=1)`),
which would also create the headroom §4.1 lacks. **Resolve this with the user.**

### 4.7 Parked experiments (local branches, NOT pushed unless you push them)
- `sa-probabilistic-swap`: make SA swaps use the Metropolis temperature instead of greedy T=0
  (textbook-correct). Gated **neutral** on the corpus. Might help on a headroom board — revisit.
- `aesthetic-v2-spacing`: even-spacing of aligned rows + `metrics.spacing_unevenness`. 133 tests green,
  0 overlaps, but small effect (9 parts on `system`) and a ~1-net routing nudge the wrong way → skipped.
  Revive if you want the visual polish and accept the tiny cost.

### 4.8 Untested ideas (from the scout doc, low prior but unproven)
Adaptive centroid resync, mid-anneal decap re-pairing, density-adaptive nudge amplitude, FD stronger
early repulsion, periodic restart-from-best, net-aware legalize. All are "coin-flips" — and note they
**cannot be validated on the current corpus** (§4.1) until headroom exists.

### 4.9 Process / repo hygiene
- The gate harness was scratchpad-only during the work; now committed to `tools/gate/` — but the
  scripts have some hardcoded paths (JAR location, board dirs passed as args). Parameterize before
  relying on them elsewhere.
- `docs/PLACEMENT_AUDIT_PROMPT.md` is an untracked pre-existing file (not this work) — left alone.

## 5. What actually changed (this session, on `main`, now pushed)
1. `engine.py`: SA step cap `min(45000,…)` → `min(90000,…)` (search-limited fix). **Win.**
2. `aesthetic.py` (new): `align()` post-pass + `_clusters`/`_try_move` + `metrics.alignment_score`;
   wired into `engine.place(aesthetic=True)` (default ON); `AESTHETIC` env in `cli.py`. **Win.**
3. Decap term weight 1.5→3.0 (`anneal._Weights.DECAP`) landed just before this session. **Win.**
4. Logs/specs under `docs/superpowers/`; gate harness under `tools/gate/`.
See `git log` on `main` and the session log for the full sequence (Waves 0–6).

## 6. Recommended plan to make it "perfect" (in order)
1. **Trust the gate.** Resolve §4.2 (is 59% real?) by inspecting unrouted nets on a small board; fix
   any harness artifact (GND/zone/ratsnest counting) and re-baseline the corpus. Fix `PYTHONHASHSEED`
   and confirm determinism (§4.4). Parameterize `tools/gate/` paths.
2. **Get measurement headroom (§4.1/§4.6).** Decide single- vs 2-sided target with the user; obtain or
   construct boards that DON'T route ~100% trivially, so engine changes are detectable. Build the gate
   that matches the real fab.
3. **Only then** re-test the parked + untested ideas (§4.7/§4.8) against the headroom gate; keep wins.
4. **Product polish:** gallery preview/final split (§4.5); optional even-spacing (§4.7); surface
   `alignment_score`/routed-% in the app gallery.
5. **Broaden validation:** run on more external boards (like OVN) to confirm generalization; consider a
   small held-out board set so you're not overfitting the DTU corpus.

## 7. Hard "do NOT" list
- Do NOT add terms to `anneal._quality`.
- Do NOT weight power nets for proximity (silent no-op); use pad-to-pad hinges.
- Do NOT trust routed-% deltas < ~3 nets, or any single-run number, as signal.
- Do NOT gate without stripping existing tracks first (§3).
- Do NOT run heavy placement concurrently with a gate route (nondeterminism risk, §4.4).
- Do NOT chase placement wins on the current corpus expecting them to show — fix measurement first.
