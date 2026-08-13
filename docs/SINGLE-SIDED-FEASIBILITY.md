# Can this tool be made genuinely good at single-sided boards?

Short answer: **yes, substantially — and there is a specific, principled reason
it is bad today that nobody has exploited.** Not "perfect", and I say why below.

---

## 1. What the problem actually is

You are asking for: *given a netlist and a board outline, produce a placement
that can be routed on ONE copper layer, using as few wire bridges as physically
necessary.*

The thing that makes this hard is that it is **two coupled problems**, and the
tool currently only attacks one of them:

| | question | current tool |
|---|---|---|
| **Topological** | Can these connections be drawn in a plane at all, without crossings? | **never asked** |
| **Geometric** | Given that they can, do the parts fit, with room for tracks? | HPWL + crossings + overlap |

Single-sided routability is, at its core, a **planarity** question. A set of
connections is routable on one layer if and only if the graph it forms can be
drawn in the plane without edges crossing. That is a property of the *netlist*,
not of any particular placement — and it is decidable exactly, in linear time.

The current engine optimises straight-line crossing counts between
minimum-spanning-tree edges. That is a *geometric shadow* of the real property.
It is why it can hand you a layout that FreeRouting then declares impossible,
while you, by hand, find a single-sided arrangement: you were unconsciously
searching the topological space (flip this part, run that net around the other
way, reorder these series resistors) and the tool never enters it.

## 2. Why this is the exploitable gap

Three facts that together make the topological approach practical:

1. **Planarity is cheap to decide.** Linear time, and I can implement it in pure
   Python with no dependencies. So the tool can *know*, before placing anything,
   whether single-sided is achievable — and if not, exactly how many connections
   must become jumpers. That number is a **lower bound no placement can beat**.
   Today the tool has no idea and simply gives up when the router struggles.

2. **The ground plane deletes the worst vertex.** Your boards pour GND. Every
   GND pad is connected for free, so the ground net vanishes from the graph
   entirely. That is typically the highest-degree node on the board — removing
   it makes planarity vastly more likely. (Measured on subxo: GND is 15 of 100
   pads.) This is exactly what you exploit by hand.

3. **Bends are free on a PCB.** There is a classical result (Pach–Wenger) that a
   planar graph can be drawn with its vertices at *arbitrary prescribed
   positions*, if edges are allowed to bend. Copper bends for nothing. So once a
   netlist is known planar, placement stops being about "avoiding crossings" and
   becomes about **realising a specific planar embedding** with enough room —
   a much better-posed problem than the proxy minimisation being done now.

> **Correction, written after the work was done.** Points 1–3 held up, but two
> specifics in the census below did not. "motor_power forces 2 bridges, system
> forces 1" were **artefacts** of contracting each part to a point: both came
> entirely from DO-41 diodes at 10.16 mm pitch, where 8.16 mm of clear copper
> separates the leads and a track walks straight between them, yet the point
> model reads the part as an obstacle tying its two nets together. Re-modelled
> pad-accurately (`escape.py`), **every board tested forces zero bridges** — the
> finding got stronger, its specifics were wrong.
>
> The larger correction is to the thesis of this document. Topological planarity
> turned out **not** to be the binding constraint: the pad-accurate graph is a
> forest on every board, so planarity never binds, and boards that fail to route
> single-sided fail on *capacity and escape room* instead — how many tracks fit
> down a corridor, whether a pad can reach the outside. Planarity remains a
> correct and cheap **lower bound**, and it is what proves those bridges are
> avoidable; it just does not predict which boards will be hard. See
> `NIGHT-LOG.md` for the measurements.

The lever a human uses and the tool does not: **the netlist graph is not fixed.**
Rotating a part, flipping it, swapping equivalent gates, reordering series
elements, and choosing which tree connects a multi-pin net all change the graph
or its embedding constraints. That search space is where "I did it by hand and
it worked" lives.

## 3. What I can realistically deliver

**High confidence — I expect these to work:**
- Exact answer to "is this board single-sided-able?", per board, in milliseconds.
- A true **lower bound on jumpers** (graph skewness), so the tool stops guessing
  and can tell you "this needs at least 3 bridges, no placement avoids them".
- Placement driven by a planar embedding rather than by crossing proxies.
- A large reduction in bridges on boards that are planar but currently botched.

**Medium confidence — real research risk:**
- Minimum jumper count when a board is *not* planar is **NP-hard** (skewness).
  Good heuristics exist and your boards are small (10–131 parts), so I expect
  near-optimal, not provably optimal.
- Realising an embedding with real footprint sizes, clearances and a fixed
  outline is a genuine geometric problem. Embedding says *where things go
  relative to each other*; making that fit a 100×100 mm board is extra work.

**The thing that might block it, and my fallback:**
- **FreeRouting is not planarity-aware.** I can hand it a placement for which a
  single-sided routing provably exists and it may still fail to find it. If that
  turns out to be the binding constraint, the fix is the one the previous
  handoff already scoped as tractable: a **single-layer maze/A\* router for
  through-hole boards with a ground plane and explicit jumper insertion**. Not a
  general autorouter — a narrow one for exactly your process. That is a big
  build, but it is bounded, and tonight's measurements will tell me whether it
  is necessary.

**What I will not claim:**
- Not "the best PCB router ever", and not perfect. Some netlists are genuinely
  non-planar and will need jumpers; the honest goal is *provably as few as
  possible*, every one deliberate and reported.
- "Smarter than an electrical engineer" is true on a narrow axis — no human
  reliably tests planarity of a 60-node graph or proves a jumper lower bound —
  and false on others. You know what the circuit *means*. I will make the tool
  beat you on the combinatorics, not on intent.

## 4. Why I believe the upside is large

From today's measurements on your boards, the evidence that placement (not the
router) is the binding constraint:

- The same board routed from its **original** placement left 25 connections
  missing; from **fresh placements** at different seeds, 4–13. Placement choice
  alone moved it by 3–6×.
- FreeRouting is **deterministic** (same placement routed 6×, identical every
  time), so these differences are real signal, not luck.
- The gallery's own ranking was picking candidates at **rho 0.01** against real
  routing outcomes — it was choosing essentially at random among placements that
  differed by up to 12 bridges. Fixed today; worth 15 fewer hand-soldered wires
  across 4 boards.

So there is a lot of headroom, and it is in placement and topology, which is
where I can operate without rewriting the router.

## 5. Tonight

Order is deliberate: cheapest and most informative first.

1. **Planarity census.** For all 15 boards: is the netlist planar with GND
   removed? If not, what is the minimum jumper count? This is fast (no routing)
   and it reframes everything — it tells us, per board, how much of the current
   bridge count is *forced* and how much is the placer's fault.
2. **Baseline.** Route every board single-sided as it stands, and from the
   placer's best candidate, so there is a number to beat per board.
3. **Parallel exploration** of the approaches that could close the gap:
   embedding-driven placement, rotation/pin-order search, net-topology choice,
   and router-side coaxing.
4. Everything measured, per board, against the routed truth — the same gate
   discipline as today. Anything that does not beat the baseline does not ship.

Progress is logged to `docs/NIGHT-LOG.md` as it happens, and every experiment is
a CLI tool you can re-run.
