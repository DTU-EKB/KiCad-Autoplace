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

**Not shipped.** This is *predicted* bridges, and predicted is not routed; this
repo has already been burned once by a metric that looked right. It is env-gated
(`ORIENT=1`) in the batch harness and a routed gate is running.

---
