# Night log — making single-sided actually work

Running record of the overnight session. Newest section at the bottom. Every
claim here has a command you can re-run.

---

## The headline, found early

**14 of 16 real boards can be routed single-sided with ZERO wire bridges — and
that is a proof, not an estimate.**

```bash
& "C:\Program Files\KiCad\10.0\bin\python.exe" tools/planarity_census.py out.json BOARD.kicad_pcb ...
```

| board | parts | nets | single-sided possible? | forced bridges |
|---|---|---|---|---|
| buck | 11 | 8 | yes | 0 |
| boost | 11 | 8 | yes | 0 |
| rectifier | 12 | 4 | yes | 0 |
| feedback_circuit | 10 | 8 | yes | 0 |
| motor_feedback | 10 | 10 | yes | 0 |
| drive_circuit | 14 | 8 | yes | 0 |
| current_sense | 17 | 10 | yes | 0 |
| boost_v2 | 17 | 10 | yes | 0 |
| boost_v2_mill | 17 | 10 | yes | 0 |
| mppt | 21 | 10 | yes | 0 |
| mppt_buck | 20 | 11 | yes | 0 |
| buck_v2 | 31 | 19 | yes | 0 |
| c2000_feedback | 47 | 23 | yes | 0 |
| **subxo** | 38 | 25 | **yes** | **0** |
| motor_power | 57 | 31 | no | 2 |
| system | 129 | 64 | no | 1 |

subxo is the board the pipeline currently spends **10 wire bridges** on and
*still* leaves 3 connections unrouted. Topologically it needs **none**. Every
one of those bridges is the placer's fault.

Why this was invisible before: the engine only ever measured *geometric*
shadows of routability — straight-line crossings between spanning-tree edges.
Single-sided routability is *topological*: the connections fit on one layer
exactly when their graph is planar. That is decidable exactly and in
milliseconds (0.2–700 ms per board above), and nothing in the tool had ever
asked the question.

Two things make it work, and both mirror what a person does by hand:

* **The net is a hyperedge.** The graph is component/net incidence — a node per
  part, a node per net, an edge for "this part has a pad on this net". Choosing
  a spanning tree per net instead would make the answer depend on an arbitrary
  choice.
* **The ground pour deletes the worst vertex.** A filled plane connects its pads
  for free, so ground leaves the graph entirely. It is usually the
  highest-degree net on the board; leaving it in reports nearly everything as
  non-planar.

**Caveat, stated honestly:** this model contracts each part to a point, so it
ignores pad geometry, component obstacles and the board outline. "0 forced
bridges" is a *lower bound* and a necessary condition — it proves no
topological obstruction exists, not that any given placement will route. The
pad-level refinement is in progress.

### What this changes

The goal is no longer "predict how many bridges we need". It is **"reach the
lower bound"**. For 14 of these boards the target is literally zero, and there
is now a per-board number to be measured against.

---

## Placing from the embedding

If the netlist is planar there is a drawing of it with no crossings at all, so
placement should *realise that drawing* rather than fight crossings with a
proxy. `topoplace.py` does it by Tutte barycentric embedding of the
component/net incidence graph — parts and net junctions relaxed together, the
largest face pinned to the perimeter.

Measured on the graph it is actually drawing, at the positions it produces:

| board | incidence-graph crossings after seeding |
|---|---|
| buck | **0** |
| buck_v2 | **0** |
| subxo | **0** |
| current_sense | 10 |
| c2000_feedback | 12 |

Three of five come out **genuinely planar** — a real single-sided drawing.

### A measurement trap worth recording

subxo's incidence drawing has **0** crossings while `globalroute` reports
**67** on the same placement. Both numbers are right; they describe different
graphs. The embedding connects each net as a *star through its junction point*.
`globalroute.net_segments` builds a *pad-to-pad minimum spanning tree*. A planar
drawing in one topology is not planar in the other.

The consequence is concrete: realising a planar placement physically requires
the net's tree to follow the embedding, not the MST. Choosing net trees is
therefore not a separate optimisation — it is the other half of this one.

The two boards that are not drawn cleanly are the expected failure: Tutte
guarantees a crossing-free drawing only for **3-connected** planar graphs, and
every netlist graph here has minimum degree 1. The standard fix (stellate each
face to make the graph 3-connected, relax, then drop the added vertices) is not
done yet.

### Keeping it

`anneal._quality` selects layouts on wirelength plus the overlap barrier, so a
crossing-free seed gets traded away for a few millimetres of wire before the
anneal returns. `cross_weight` charges per crossing in the selection metric.
Defaults to 0.0 — it ships only if it beats the baseline on routed boards.

---

## Early baseline (routed, single-sided, current pipeline)

| board | seeds | best bridges | seeds closing with zero bridges |
|---|---|---|---|
| buck | 6 | 0 | 4/6 |
| boost | 6 | **1** | 0/6 |
| rectifier | 6 | 0 | 6/6 |
| feedback_circuit | 6 | 0 | 5/6 |
| motor_feedback | 6 | 0 | 1/6 |
| drive_circuit | 4 | 0 | 1/4 |

`boost` is the shape of the problem in miniature: provably 0 forced bridges, yet
every one of six placements needed at least one. Nothing about the circuit
requires it.

---

## Topology seeding: negative on the proxy, positive on the routed truth

Crossings of the MST net trees, current engine vs embedding seed
(`tools/eval_topoplace.py`, 4 seeds):

| board | engine | topo seed | topo + anneal |
|---|---|---|---|
| boost | 1..8 | 12 | 2..5 |
| mppt_buck | 5..11 | 17 | 4..9 |
| buck_v2 | 7..17 | 11 | 18..26 |
| motor_power | 38..56 | **23** | 31..70 |
| subxo | 34..55 | 67 | 38..52 |

On this yardstick topology seeding is **not** an improvement — only motor_power
clearly gains. Recorded as a negative result, because it is one.

But the yardstick is measuring the wrong graph (see the trap above), so it does
not settle anything. The routed comparison does, and the first result points the
other way:

> **`boost`, routed single-sided for real: the embedding seed closed it with 0
> bridges. No baseline placement did — best of six was 1.**

So a placement that looks *worse* on MST crossings routed *better*. That is a
warning about the proxy as much as a result about the seed: MST crossings were
the best cheap predictor available (Spearman 0.61), and they still mis-rank a
placement built on a different net topology.

Verdict deferred until the full routed sweep lands. Nothing about topology
seeding is on by default: `strategy="auto"` and `cross_weight=0.0` remain the
shipped behaviour.

### boost, routed per seed — the first clean win

`(seed, connections still missing after the single-sided route, bridges, final)`

```
baseline  (0,1,1,0) (1,1,1,0) (2,2,2,0) (3,1,1,0) (4,1,1,0) (5,1,1,0)
topo      (0,1,1,0) (1,0,0,0) (2,0,0,0) (3,1,1,0)
xw50      (0,1,1,0) (1,1,1,0) (2,1,1,0) (3,1,1,0)
```

boost forces **0** bridges. The baseline never reaches that — six placements,
six bridges. Topology seeding reaches it on **two of four** seeds: those boards
route completely on one copper layer with nothing to hand-solder.

Two things follow:

* The win is qualitative and reproducible, not one lucky seed.
* `cross_weight` on its own does nothing here (4/4 still need a bridge). Starting
  from the right topology matters more than defending it during the anneal —
  which is the opposite of what I expected, and worth remembering before
  spending effort on making the anneal topology-aware.

That also exposed a bug: `topoplace` ignored the RNG, so those four "different"
placements were one placement annealed four ways. The seed now selects which
face of the embedding becomes the outer one — a real source of variety, since
every face choice is a different and equally crossing-free drawing.

---

## Orientation: rotate parts without moving them

Which way a part faces decides which of its pads sit near which neighbours.
Flipping a 2-pin resistor can uncross two nets for free — nothing moves, no
part is displaced, no overlap can appear. The annealer does propose rotations
but judges them by HPWL and overlap, so it has no pressure to untangle.

`orient.py` sweeps orientation alone, on a lexicographic
`(min_bridges, conflicts, tree length)` objective — bridges lead because they
are what gets soldered, but a vertex cover is coarse and stalls on plateaus, so
crossings supply the gradient underneath. Rotations are *refused* rather than
repaired, so the pass is safe to run after `legalize`.

Predicted bridges, mean over 6 seeds:

| board | before | after |
|---|---|---|
| buck_v2 | 8.00 | **5.83** |
| c2000_feedback | 9.50 | **7.00** |
| mppt_buck | 4.83 | **3.00** |
| current_sense | 5.33 | **4.00** |
| motor_power | 20.33 | **16.00** |
| subxo | 14.17 | **11.33** |

33 of 36 seeds improved, none regressed, no overlaps, ~1.65% of placement time.
Cost is ~5% more half-perimeter wirelength, though the routed tree length is
near-neutral (−0.7% to +2.1%) — most of that is pads moving as parts turn.

**Not shipped, and now we know why.** The routed gate came back and the
predicted gain did not convert:

| board | base | with orientation |
|---|---|---|
| boost | 1 bridge, 6/6 closed | 1 bridge, 3/4 closed |
| current_sense | never closed | never closed |
| mppt_buck | never closed | 4 bridges, 1/4 closed |

No board improved. On mppt_buck it closed at 4 bridges where topology seeding
closed the same board at **0**, so it is also the weaker of the two options
where both finish. Second time in this codebase that a metric-only improvement
failed a routed gate — the orientation pass keeps its `orient_pass=False`
default and `orient.optimise`'s docstring records the numbers, so nobody
switches it on for the encouraging prediction alone.

---

## A crossing test that disagreed with itself

Found while reconciling two implementations of the same geometry.
`globalroute._crosses` tested only for a *proper* crossing:

```python
return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))
```

A zero determinant reads as "negative" there, so a degenerate touch counted as
a crossing or not **depending on which way round the two segments were
written**. Over grid-aligned pairs, 4.6% disagreed with themselves under
argument or endpoint reordering — and every one was a T-junction or a collinear
overlap.

Both halves matter. The order dependence made every crossing count a function
of iteration order. And the mishandled case is the worst one on a real board: a
zero determinant means one net's copper passes exactly through another net's
pad — a **short** — and it was being scored as "no crossing", the most
favourable reading available. Parts sit on a 2.54 mm grid and `aesthetic.align`
snaps them into rows, so this is routine, not a curiosity.

Now the standard orientation test with the collinear cases explicit, symmetric
in both arguments and in each segment's endpoint order, asserted over thousands
of grid-aligned pairs.

---

## Net topology: a better estimator, not yet a better board

`nettopo.py` re-trees each net to minimise crossings instead of length. The
structural insight is that with all other nets fixed, a net's crossing count is
a plain *sum* over its tree edges, so the optimal re-tree is a single Prim run
under `cross_mm * crossings + length` — exact at any pin count, no enumeration.

Over 14 boards / 68 placements: crossings **−30.8%**, predicted bridges
**−18.5%**, +4.2% wire, **0 placements worse**. Against 18 routed placements it
is a measurably better *estimator* — bridge mean absolute error 3.50→2.30 and
5.12→3.38; the MST estimate was systematically pessimistic, claiming 3–9 bridges
on boards that force zero.

But it moves no parts, so **nothing on any board is better today**. It improves
the number the tool reports, and possibly how candidates rank (Spearman up on
both boards, but bootstrap intervals straddle zero on 18 samples). Not wired in.

---

## Routed scoreboard so far

Bridges the best placement of each variant actually needed, against the floor:

| board | forced | base | topo | xw50 | topo+xw | orient |
|---|---|---|---|---|---|---|
| boost | 0 | 1 (0/6 at 0) | **0** (2/4 at 0) | 1 | **0** (1/4) | 1 |
| mppt_buck | 0 | – | **0** (1/4) | no close | **0** (1/4) | no close |
| current_sense | 0 | no close | no close | no close | 1 | no close |
| buck_v2 | 0 | – | no close | no close | no close | – |
| c2000_feedback | 0 | – | no close | no close | – | – |
| buck / rectifier / feedback_circuit / motor_feedback / drive_circuit | 0 | 0 | – | – | – | – |

**Topology seeding is the only variant that reaches the proven zero-bridge floor
on a board the baseline cannot** — now on two of them, boost and mppt_buck.

The open problem is the larger boards: buck_v2, c2000_feedback and current_sense
force zero bridges and *no variant closes them at all*. Either FreeRouting
cannot find single-sided routings that provably exist, or the point-model misses
a geometric obstruction that `padblock` would see. That question decides whether
placement work can finish the job or whether a dedicated single-layer router is
required.

---

## Single-sided is now an attempt, not a goal

The tool was pushing for single-sided and then quietly falling back to two
layers *inside* the bridging step, which reads as a success. `advise.py`
separates what was tangled:

| | kind |
|---|---|
| `single_sided_possible`, `forced_bridges` | **exact** — from the netlist, holds for every placement |
| `difficulty` | **estimated** — whether *this placer, today* finds such a layout |
| `recommend` | those plus the user's own tolerance for hand-soldered wires |

Single-sided is **always attempted regardless of the recommendation**, because
trying is cheap next to being wrong and the result is fact rather than
prediction. `try_single_sided_first` is a field, not an implication.

The part-count bands are fitted to the routed study above, not chosen for
roundness — attempted-and-closed at 10, 10, 11, 11, 12, 14, 17, 20 parts;
attempted-and-failed at 31, 38, 47, 58. Nothing was measured between 21 and 30,
exactly where the boundary sits, so that gap is reported as "moderate, worth
attempting" instead of guessed. Pad density was tried first and does **not**
separate the groups (drive_circuit closes at 1.14 pads/cm² while boards at half
that do not), so part count is used alone rather than dressed up with a term the
data will not support.

`cli.py advise BOARD` gives all of it before anything is placed.

---

## Writing our own single-layer router

Two agents, two genuinely different approaches, both measured against
FreeRouting on the *same placements* with the same `unrouted.analyse` DRC count,
and both against `planarity.forced_bridges` as the floor:

* **`gridroute`** — conventional Lee/A\* maze router with rip-up and reroute.
  The workhorse; net ordering and rip-up are where routing quality lives.
* **`toporoute`** — routes *along the planar embedding*. The embedding already
  says, for each junction, the cyclic order connections leave it and which face
  each lives in. That is exactly the information a maze router lacks and
  rediscovers badly by search.

What makes the narrow scope winnable: one layer (no vias, no layer assignment),
ground needs no routing at all (the pour connects it), obstacles are only pads
and the board edge since copper runs *under* component bodies, and anything that
genuinely cannot be routed becomes an explicitly counted wire bridge rather than
a silent failure.

Explicitly not the goal: a general autorouter.

### Answered before either finished: don't write one

`tools/probe_freerouting.py` settled it with proofs rather than opinion. 38
synthetic single-layer THT boards at the CNC profile, each **shipping a
hand-built complete routing on one copper layer**, each verified by kicad-cli
DRC at 0 missing and 0 clearance errors — so on 37 of them a single-sided
routing provably exists *for that exact placement*. Families cover the
genuinely global cases: concentric nested arcs, two-page book embeddings,
reverse buses that must go the long way round, 1.9 mm lanes against a 1.85 mm
minimum, multi-pin tap buses, wheels, GND-pour variants.

210 routes through the engine's own `route_once(sides=1)`:

| `-mp` passes | closed with 0 missing |
|---|---|
| 1 | 29/37 |
| 5 | 32/37 |
| **10** | **33/37 (89%)** |
| 30 | 33/37 |
| 100 | 33/37 |

Above 10 passes there is **zero** further gain, and wall clock is flat at
26–28 s regardless because JVM startup dominates. The engine's default of 10 is
correct. Other flags (`-us global`, `-us hybrid`, `-is seq`, `-is rand`,
`-inc`) give identical results; **`-oit 0` hangs** — never use it.

The four boards it never closes miss **exactly one** connection each, at every
setting. Real boards routed single-sided from their shipped placement miss 4 of
13 (buck), 2 of 13 (rectifier), 7 of 23 (current_sense), 11 of 50
(c2000_feedback) — **2 to 11× more than FreeRouting demonstrably leaves on the
table when a solution exists.** A home-grown router would buy about one
connection per board. Both router agents were stopped.

Validity: every routed board confirmed strictly one layer with 0 vias; ratsnest
and DRC agreed on all 210 routes; and the negative control had to be tightened
after its first version turned out to be genuinely routable — negative controls
need checking as hard as positive ones.

---

## The actual cause: footprint geometry, not routing

The probe surfaced something more important than the router question.

On the CNC profile a **2.54 mm pin header with 2.0 mm pads has a 0.54 mm
pad-to-pad gap** — below the 0.85 mm minimum clearance. It produced **28 DRC
clearance errors on a board whose routing was otherwise perfect**. The board was
unmanufacturable for footprint reasons alone, with no routing involved.

And passing a 1.0 mm track between two 2.0 mm pads needs **4.7 mm** centre to
centre, so no track can ever pass between adjacent header pins. **A component's
pad row is a wall copper cannot cross.**

That makes `planarity.forced_bridges` optimistic in a specific, fixable way: it
contracts each part to a *point*, so it believes copper passes freely through a
footprint. This is the leading suspect for boards the census calls "0 forced
bridges" that still refuse to route single-sided. `padblock.py` already computes
which gaps are genuinely impassable; the model needs to use it.

### The pad-accurate model was built. It does not explain the failures.

`escape.py` rebuilds feasibility with each part as its *ring of pads*, joined
only across gaps `padblock` says no track fits through — so a DIP's open centre
corridor stays open. Result: **zero forced bridges on all 15 boards, including
all four known failures.** It does not separate them at all, and it fails in the
opposite direction to the hypothesis.

The reason is structural, not tuning. Contracting a part to a point is what
*creates* the cycles that could be non-planar; restoring the pads leaves a
forest, and forests are always planar:

| | buck | buck_v2 | c2000 | motor_power | subxo | system |
|---|---|---|---|---|---|---|
| E−V, pad model | −2 | −2 | −13 | −9 | −4 | −9 |
| E−V, point model | +2 | +9 | +3 | +18 | +12 | +39 |

**It also falsifies two entries in my own census.** motor_power's "2 forced
bridges" and system's "1" come entirely from **DO-41 diodes at 10.16 mm pitch** —
8.16 mm of clear copper between the leads, which two tracks fit through. The
point model treats such a part as an obstacle tying its two nets together. The
honest reading is that **all these boards force zero bridges**; the earlier
"14 of 16" understated it. `planarity.forced_bridges` now documents the
over-report.

So **topological planarity is not the binding constraint.** What defeats
buck_v2, c2000_feedback and motor_power must be *capacity and escape room* — how
many tracks fit down a corridor, whether a pad can reach the outside — which no
planarity of any pad-level graph can express.

One exception worth keeping: on **subxo** the placement itself closes 35
inter-part gaps and does make the pad graph non-planar (6+ bridges). That is
geometry rather than topology, and it only appears once a placement is dense.

The pessimistic bracket (part = solid obstacle) fires on 4/4 known failures —
but also on three boards that close. Fisher exact p ≈ 0.07: suggestive, not
established, and physically wrong since it walls the DIP corridor.

### Footprint audit — read it against your own design rules

Geometrically, a 2.54 mm header with 1.7 mm lands leaves a **0.840 mm** gap
against a 0.85 mm netclass clearance: short by **0.010 mm**. Nine footprint
classes across the boards are in that state.

But `kicad-cli` **does not load `.kicad_dru`** (the handoff says so, and this is
exactly the trap): `subxo.kicad_dru` already grants that exception deliberately,
with the reasoning that 0.84 mm still clears the 0.8 mm end mill in a single
pass and the gap is a property of the part rather than of anything we cut. So on
subxo and system these are **not defects**.

Only 2 of 15 board projects carry a `.kicad_dru`. For the other 13 the gap will
flag in KiCad's own DRC — and the better fix is to copy subxo's exception
pattern rather than shrink the land, since 0.84 mm is already manufacturable and
a smaller pad costs annular ring.

---
