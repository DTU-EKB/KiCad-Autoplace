# HANDOFF — routability-driven placement (global router + jumper minimisation)

> Read this whole file first. It is self-contained: you do not need the session
> that produced it. It covers what the tool is, what was changed on 2026-08-13
> and why, the traps that cost real time, and the project to build next.

---

## The goal in one paragraph

KiCad-Autoplace places components and routes them with FreeRouting. Placement is
currently chosen by **proxies** — half-perimeter wirelength, net crossings,
overlap — and routing feedback only arrives at the very end, at 10–60 s per
route. The job is to add the **missing middle tier**: a fast, pure-Python
**global router** that estimates congestion (and, on single-sided boards, the
number of wire bridges a placement forces) in milliseconds, so the annealer can
optimise against real routability thousands of times per run instead of eight.

---

## Where everything is

| Thing | Path |
|---|---|
| Repo | `C:\Users\Mads2\KiCad-Autoplace` (`git@github.com:DTU-EKB/KiCad-Autoplace.git`) |
| Engine (pure Python, no pcbnew) | `plugin/plugins/autoplace/` |
| Only modules touching pcbnew | `kicad_io.py`, `routing.py`, `outline.py`, `completer.py`, `unrouted.py` |
| Headless CLI | `cli.py` — `place`, `place-multi`, `refine`, **`complete`**, `finalize`, `preflight`, `metrics`, `dump` |
| Tests (no pcbnew, no Java) | `tests/` — 148 passing |
| Desktop app | `app/` (Electron; `npm start`) |
| Test board used throughout | `Projects/Bose Sub Integration/hardware/kicad/subxo.kicad_pcb` (38 THT parts, single-sided, CNC-milled) |

**Toolchain — check these first.** KiCad **10** (`C:\Program Files\KiCad\10.0`;
8.0 and 9.0 are also installed, and their stock libraries differ — see
"Traps" below). Java 21. FreeRouting **1.9.0** at
`~/.freerouting/freerouting-1.9.0.jar`. Python 3.13 for tests, KiCad's bundled
`bin\python.exe` for anything importing `pcbnew`.

```powershell
py -3.13 -m pytest tests/ -q                      # 148 passing, no pcbnew needed
& "C:\Program Files\KiCad\10.0\bin\python.exe" cli.py complete board.kicad_pcb
```

---

## What changed on 2026-08-13 (3 commits, on `main`, pushed)

`b6caa07`, `7b5d7b9`, `3877e74`. All three came out of running the pipeline on a
real board and measuring, not from reading code.

### 1. Single-sided routing was destroying the ground plane

`routing._flip_to_bottom` moves the pour F.Cu → B.Cu after routing. Moving a zone
between layers invalidates its fill and KiCad drops the filled polygons on save,
so **every single-sided board this tool ever produced shipped an outline-only
pour that connected nothing**. Two costs: every GND pad read as unrouted
ratsnest, and the etched board had a physically floating ground plane.

Fixed by refilling after the move. Measured on the test board: filled polygons
**0 → 3**, and DRC on the delivered file now agrees with DRC `--refill-zones`
(both 9) instead of disagreeing by 11.

### 2. Completion was measured on a board the user never receives

The percentage was computed **before** the flip. The refine loop steers on that
number, so it was optimising against the wrong board. Now measured after.

### 3. The completion metric was pessimistic, hiding a finished board

`pcbnew`'s ratsnest count is computed on the pour exactly as saved; KiCad's DRC
refills zones first and routinely finds **fewer** missing connections. On a
finished board the ratsnest said **2 missing** and DRC said **0** — the board was
complete and the tool could not tell, so it kept escalating and then reported
failure. `completer` now takes the count from `unrouted.analyse` (DRC +
`--refill-zones`) and falls back to the ratsnest only if `kicad-cli` is absent.

**This is the single most important lesson for the next project: fix the metric
before optimising against it.** Any candidate scoring wired to the old number
would have been ranking noise.

### New capabilities added

| Module | What it does |
|---|---|
| `unrouted.py` | *Which* connections failed, not just how many. Parses `kicad-cli pcb drc --format json` into `MissingLink(net, ref_a, pad_a, x, y, ref_b, …)`. Needed because `GetRatsnestForNet` returns an opaque `SwigPyObject` on KiCad 10. |
| `completion.py` | Pure escalation policy: `next_action()` → `place` / `grow` / `bridge` / `done` / `stop`. Fully unit-tested without FreeRouting. |
| `completer.py` | Wires that policy to the real annealer, router and board. |
| `outline.py` | Read/grow/shrink the Edge.Cuts rectangle; `required_growth()` answers "is the outline itself the problem?" |
| `routing.route_stage2_bridges()` | Locks stage-1 copper (`SetLocked` → exported as `type=fix`), enables the second layer, routes only the leftovers. Whatever lands on F.Cu is a wire bridge, counted as connected **runs** (union-find on endpoints), not raw segments. |
| `tools/diag_unrouted.py` | Route a board once and print every missing connection by name. |

### Measured result on the test board

```
route:  84.7% (9 missing)
place:  81.4% (11 missing)   -> worse, escalated correctly
grow:   86.4% (8 missing)
bridge: 94.9% (3 missing, 5 bridges)
bridge: 100%  (0 missing)     <- confirmed by DRC on the output board
```

**5 wire bridges** on a 38-part single-sided board. That is a reasonable answer,
not a failure: an arbitrary netlist is essentially never planar.

---

## Traps that cost real time (do not rediscover these)

**KiCad / pcbnew**
- `GetRatsnestForNet()` returns an opaque `SwigPyObject` on KiCad 10 — no
  iterable edges. Use the DRC json route (`unrouted.py`).
- `GetTracks()` is **not iterable** after `ImportSpecctraSES`. Save, reload, then
  iterate.
- In-process track/zone removal access-violates. Strip routing **textually**
  (`strip.py`).
- Moving a zone between layers invalidates its fill. **Always refill before
  saving.**
- `kicad-cli pcb drc` **does not load the project's `.kicad_dru`** — verified by
  feeding it a blanket 0.1 mm clearance rule and seeing the violation count not
  move. Its clearance output therefore includes violations the GUI suppresses by
  design rule. Missing *connections* are unaffected (geometry, not rules).
- Always pass `--refill-zones` to DRC, or an unfilled pour reads as dozens of
  missing connections.
- KiCad 10 **deleted** the `bornier` terminal-block footprints that 9 shipped
  (`TerminalBlock.pretty`: 56 → 45). Several KiCad versions coexist on this PC;
  always resolve footprints against the version that actually opens the project.

**FreeRouting**
- Use **1.9.0**, not 2.0.1 (its version check NPEs and never writes the SES) and
  not 2.2+ (needs Java 25).
- DSN files must be **BOM-free**.
- It does **not** re-emit fixed wires in its session file, so a raw SES import
  deletes locked stage-1 copper. Snapshot tracks first and re-add what vanished.
- ~~Results are **not perfectly reproducible** run to run.~~ **Corrected
  2026-08-13 (later session).** Measured with `tools/router_noise.py`: the same
  placement routed 6 times gave *identical* results every time (stage-1 missing
  5/5/5/5/5/5, bridges 3/3/3/3/3/3, sd = 0.00). FreeRouting 1.9.0 is
  deterministic for a fixed DSN and pass count, so routed outcomes **can** be
  compared candidate to candidate. The earlier "noise" was almost certainly the
  DRC parser bug below, which made the same board report different counts
  depending on which items KiCad happened to name.

---

## The project: a fast global router

### Why

| Tier | Evaluator | Cost | Evaluations per run | Status |
|---|---|---|---|---|
| inner | HPWL, crossings, overlap (`metrics.py`) | µs | 10⁵–10⁶ | exists |
| **middle** | **global router / congestion + jumper estimate** | **ms** | **10²–10³** | **missing** |
| outer | FreeRouting + DRC (`routing.py`, `unrouted.py`) | 10–60 s | 5–20 | exists |

Routing cannot go in the annealer's inner loop — one route is 10–60 s. The middle
tier is the whole point: real routability feedback, thousands of times per run.

### What to build

A pure-Python module (suggested `autoplace/globalroute.py`) that takes a `Board`
model and returns:

1. **A congestion map** — coarse grid (start ~2–5 mm), each net assigned to a
   corridor, per-cell demand vs capacity. Capacity comes from the fabrication
   profile: track 1.0 mm + 2 × clearance 0.85 mm ≈ 2.7 mm per track on the CNC
   profile (`fabrication.py`).
2. **A planarity / jumper estimate** — for single-sided boards, which net pairs
   are forced to cross, and therefore the **minimum number of wire bridges** this
   placement implies. This is the number the user actually cares about, and it is
   computable without routing anything.

Then wire it into `anneal.py`'s cost function alongside the existing terms, and
into `ranking.py` so the gallery ranks candidates on predicted bridges rather
than on crossings-as-a-proxy.

### Acceptance criteria

- **Correlation, first and foremost.** Before wiring it into the annealer, show
  that the estimate predicts FreeRouting's real outcome. Take 10–20 placements
  (`place-multi` with different seeds gives them free), route each for real,
  and plot predicted vs actual. A predictor that does not correlate is worse than
  no predictor — it will confidently steer placement the wrong way.
- Pure Python, no `pcbnew`, no Java → unit-testable like the rest of the engine.
- Fast enough for the inner loop: target < 10 ms for a 40-part board.
- Deterministic: same model + same seed → same numbers.

### Suggested order of work

1. Build the grid + corridor assignment with tests on synthetic boards.
2. Add the planarity/crossing analysis and the bridge estimate.
3. **Validate correlation against real FreeRouting runs** (the gate — do not skip).
4. Wire into `anneal.py` cost and `ranking.py`.
5. Re-measure `cli.py complete` on the test board: fewer bridges, fewer
   escalation rungs, less wall-clock.

### Explicitly *not* the project

Replacing FreeRouting with a home-grown detailed autorouter. It is ~100k lines
of decades-refined algorithms, and none of the three defects found on 2026-08-13
were routing defects — they were in the layer around the router. If a detailed
router is ever wanted, the tractable scope is a **single-layer maze/A\* router
for THT boards with a ground plane, with explicit jumper insertion** — not a
general autorouter.

---

## RESULT of that project (2026-08-13, later session)

The global router was built, validated, and **rejected as a cost-function
input**. The gate did its job. Read this before rebuilding it.

### What was built

`plugin/plugins/autoplace/globalroute.py` — pure Python, no pcbnew, no Java,
~2.7 ms for a 40-part board, 30 unit tests. It gives the two things asked for:

* **congestion** — coarse grid, per-cell demand (net-tree edges rasterised) vs
  capacity (`cell / (track + clearance)`, derated by the THT pads in the cell);
* **bridges** — the minimum wire bridges a single-sided placement forces,
  computed as the **minimum vertex cover of the crossing graph** (segments are
  vertices, crossings are edges). That is the right model: one lifted wire
  clears *every* crossing along it, so a segment cutting across five others is
  one bridge, not five. Solved exactly by branch and bound, checked against
  brute force on 45 graphs including K5.

### Why it is not in the cost function

76 placements across **4 boards** (subxo 40 seeds, buck_v2 12, c2000_feedback 12,
motor_power 12) were routed for real — stage-1 single-sided, then stage-2
bridging — and scored against the bridges each placement actually forces.
Correlations are computed **per board and then averaged**; pooling boards would
mostly measure "which board is this", since a 58-part board needs more bridges
than a 31-part one whatever the placement.

Mean per-board Spearman vs required bridges:

| predictor | mean rho |
|---|---|
| `crossings` (the proxy that was already there) | **0.614** |
| `gr_conflicts` (power nets excluded) | 0.588 |
| `gr_bridges` (power nets excluded) | 0.581 |
| `hpwl_mm` | 0.568 |
| `gr_bridges` (all routed nets) | 0.377 |
| `gr_overflow` (congestion) | 0.440 |

And as a *combination*, which is what "wire it in alongside the existing terms"
would mean:

| ranking signal | mean rho |
|---|---|
| rank(`crossings`) + rank(`hpwl`) — **both already existed** | 0.627 |
| rank(`crossings`) + rank(`hpwl`) + rank(`gr_bridges`) | 0.611 |

Adding the global router to the two existing metrics makes the ranking **worse**.
It correlates (rho 0.58, positive on all four boards) but it is not carrying
information that HPWL and crossings do not already carry. Two things worth
knowing if you try again:

* **Excluding power nets matters more than the clever part.** `gr_bridges` goes
  from 0.377 to 0.581 purely by dropping power rails. A 7-pin rail's tree edges
  span the whole board and cross everything regardless of placement detail, so
  they swamp the signal from the signal nets.
* **The vertex cover compresses the signal.** Conflicts range 20–55 across
  placements while their cover ranges only 10–19, so distinguishable placements
  collapse onto the same integer. It is the physically correct quantity and a
  worse *ranking* statistic. Report bridges; rank on something finer.

Reproduce with `tools/correlate_globalroute.py` (routes a board at N seeds,
records predictions + ground truth + the full placement geometry) and
`tools/score_correlation.py` (replays the saved geometry against any predictor
variant, per board, with bootstrap CIs). Routing is the expensive half and is
done once; predictor tuning is offline and instant.

### What the study *did* pay for

**`ranking.candidate_key` was ranking candidates at rho 0.01 — no better than
shuffling them.** The key was lexicographic:

```
(overlaps, sheet_spread, pinch_fraction, decap_proximity, hpwl_mm, seed)
```

A lexicographic key is decided by the first term that discriminates, and
`pinch_fraction` / `decap_proximity` are near-continuous, so one of them settled
the whole order before `hpwl_mm` was ever compared. Neither predicts routability:
`pinch_fraction` scores rho 0.05, `decap_proximity` **-0.18** (i.e. it was
actively pointing the wrong way).

Now: `overlaps` as a hard legality gate, then the summed **fractional ranks** of
`crossings` and `hpwl_mm` (fractional ranks so neither wins on scale — crossings
is a count in the tens, HPWL is millimetres in the hundreds), then the aesthetic
and electrical terms as genuine tie-breakers, then seed.

| | old key | new key |
|---|---|---|
| mean rho vs required bridges | 0.010 | **0.629** |
| excess bridges on the top pick, 4 boards | 21 | **6** |

Per board, bridges the user hand-solders on the gallery's top pick (best
available in brackets): buck_v2 2 → **0** [0], c2000_feedback 7 → **3** [1],
motor_power 12 → **7** [5], subxo 6 → **2** [0].

`cli.py` also had to start carrying `crossings` through to the ranker — it was
computed, displayed in the app, and then dropped from the dict `pre_rank` sees.

### A metric bug that mattered more than the router

`unrouted.parse_drc_report` matched only **pad** endpoints. KiCad reports an
unconnected item between the two objects flanking the gap, and on a routed board
those are routinely a `Track` or the ground `Zone` — so most entries were
silently discarded:

```
Track [/VIN] on B.Cu, length 15.87 mm  <->  PTH pad 1 [/VIN] of J4   dropped
PTH pad 2 [/GND] of R9                 <->  Zone [/GND] on B.Cu      dropped
```

`completer` trusts that count over the ratsnest, so **boards with up to 13
missing connections were reported as 100% complete**. The board the earlier
session delivered as *"100% (0 missing) — confirmed by DRC on the output board"*
in fact has **10 missing connections** (5 GND pads not reaching the pour, plus
`/OUT1`, `/VG_DIV`, `/POT_W` and two `/VGND` gaps). Check it with
`unrouted.analyse` before believing any completion figure from before this fix.

This also **removes the premise of commit `3877e74`**. "DRC refills zones and
routinely finds fewer missing connections than the ratsnest" was the parser
dropping entries, not DRC being smarter. With the parser fixed the two agree
**exactly** on all 16 boards checked (4–13 missing each). DRC is still the right
source — it refills zones and it can name *which* connections are missing — but
if the two ever diverge again, something is broken; `completer` now says so.

**The lesson from the earlier session held, and cost the same time twice: fix the
metric before optimising against it.** The first 16-seed run also flattered the
predictor (`gr_bridges` rho 0.66 on subxo at n=16, 0.46 at n=40) — a difference
of 0.1 in rho at n≈16 is noise, and the head-to-head bootstrap said so.

### If you pick this up again

* Don't re-run the correlation harness on one board. One board and 16 seeds
  produced a confident, wrong answer twice over.
* The unexploited signal is topological, not geometric. Straight-line MST edges
  over-predict by ~3x because the router detours; the "discount crossings within
  N mm of an endpoint" hack captured some of that (rho 0.84 on one board) but was
  wildly unstable across N and across boards — classic overfitting on 16 points.
  A tree chosen to *minimise crossings* rather than length is the principled
  version, and is untried.
* Stage-2 bridging does **not** reach 100%: across the 76 routed placements it
  left 1–6 connections still missing every time. "Bridges" is therefore "what
  FreeRouting laid on the top layer in one pass", not "what finishes the board" —
  a noisy target, and worth fixing before it is used as a gate again.
* `anneal._quality` was deliberately **not** touched. The predictor that was
  supposed to go there failed its gate, and the handoff's own warning about
  adding terms to `_quality` stands. Adding `crossings` to it is the obvious next
  experiment and needs its own place-and-route gate — the harness is ready.

---

## Open items inherited from today

- **41 untracked `_placemulti_cand*` scratch files** sit in the repo root
  (`.dsn`/`.ses`/`.kicad_pcb`/`.routed.*` from gallery runs). They should be
  gitignored (`_placemulti_cand*`) and deleted.
- **Uncommitted UI work** in `app/main.js`, `app/renderer/index.html`,
  `app/renderer/renderer.js`: a "gallery route-check" dropdown wiring
  `ROUTE_TOPK` through to `place-multi` so the top N candidates are routed for
  real before ranking. This is the same idea as this project, applied to
  candidate selection — finish or fold it in.
- The escalation ladder still spends a FreeRouting run on growth steps that turn
  out not to help. It reverts the growth now, but it pays for the attempt. A
  working global router would predict this and skip it.
- `completion.growth_needed()` is deliberately coarse (one step ≤ 8 missing, two
  above). Revisit once the bridge estimate exists.

## Related work in the neighbouring repo

The board this was all measured on lives in `Documents/Projects/Projects/Bose Sub
Integration/hardware/kicad/`, and the surrounding CNC process (0.8 mm end mill,
0.85 mm clearance, footprint gap audit, `.kicad_dru` exceptions, production
export) is documented in the `kicad-laser-pcb` Claude skill at
`C:\Users\Mads2\.claude\skills\kicad-laser-pcb\`. That skill's
`references/gotchas.md` shares several of the traps listed above.
