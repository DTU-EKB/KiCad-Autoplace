# Audit & Improve: make KiCad-Autoplace placement "senior-engineer" quality

You have full access to this repository. Your job is to **audit the automatic
PCB component-placement engine and propose, concretely, how to make its
placements good the way a senior PCB engineer's placements are good** —
deliberate, electrically sound, routable, and visually clean — instead of
mathematically optimized but scattered and random-feeling.

Read the actual code before forming opinions. Cite specific files and functions.
Do not hand-wave ("just add ML"); propose things that fit *this* codebase and the
realities of PCB design.

## What the tool is

An Electron desktop app plus a **pure-Python placement engine**. It reads a KiCad
`.kicad_pcb`, places the components (translate + rotate only — it never edits the
schematic), and routes via FreeRouting. The engine core is `pcbnew`-free and
unit-tested; only `kicad_io.py` / `routing.py` touch KiCad.

## Where the placement logic lives (start here)

- `plugin/plugins/autoplace/engine.py` — `place()` pipeline (seed → anneal → legalize)
- `plugin/plugins/autoplace/anneal.py` — simulated annealing + the `_quality()` selection metric
- `plugin/plugins/autoplace/forcedirected.py` — force-directed initial seeding
- `plugin/plugins/autoplace/floorplan.py` — hierarchical (per schematic-sheet) region floorplanning
- `plugin/plugins/autoplace/blocks.py` — functional block detection
- `plugin/plugins/autoplace/metrics.py` — HPWL, net crossings, power-net hints
- `plugin/plugins/autoplace/legalize.py` — overlap removal / legalization
- `plugin/plugins/autoplace/edge.py` — connector → board-edge placement
- `plugin/plugins/autoplace/congestion.py` — routing-congestion field parsed from FreeRouting SES
- `plugin/plugins/autoplace/refine.py` — route-driven refinement loop
- `plugin/plugins/autoplace/model.py` — `Board` / `Component` / `Pad` data model (what data is currently available)
- `plugin/plugins/autoplace/kicad_io.py` — what we read from KiCad today (and what more we *could* read)
- `docs/BUILD_SPEC.md` and `docs/superpowers/specs/` — design history and rationale

## The core problem

The engine optimizes **proxies**: half-perimeter wirelength (HPWL), net crossings,
overlap, and routing congestion. An HPWL-minimal layout can still look random and
violate how a human places a board. We want placements that are:

1. **Not random** — deterministic, explainable, consistent with engineering intent.
2. **Senior-engineer-like** — signal flows in a sensible direction (e.g. input → processing → output), related parts grouped, decoupling caps sitting at their IC's power pins, power stages coherent, sensitive analog kept away from noisy switching, connectors/IO on edges, neat alignment and consistent orientation.
3. **Functional first, pretty second** — routable, manufacturable, thermally and electrically sane; aesthetics follow from that, not the reverse.

## What I want from you (the deliverable)

A written report (Markdown) with these sections:

**A. Current-state assessment.** How placement works now, what each stage
optimizes, and *why* the output can feel random. Be specific — name files,
functions, and the exact terms in the cost/quality functions.

**B. Gap analysis.** Enumerate the placement principles a senior engineer applies
that the engine ignores or only weakly captures. For each: why it matters
electrically/manufacturing-wise, and whether the information needed is already in
the data model or must be extracted from KiCad (pad electrical type, net names &
**net classes**, pin function, footprint/component class, refdes semantics,
schematic hierarchy, courtyards, power vs. signal nets, differential pairs,
thermal pads, mechanical/keepout constraints).

**C. Proposals.** Concrete, ranked techniques to close the gaps, scoped to this
codebase. For each: the idea, which files it touches, what new data / heuristics /
constraints it needs, expected effect, risks, and rough effort. Cover at least:
cost/constraint-function design; seeding strategy; functional grouping &
hierarchy; component orientation; alignment & spacing aesthetics; and
electrical-aware rules — decoupling-cap-to-pin, power/ground topology and current
loops, analog/digital/RF separation, crystals/oscillators near their IC, high-pin
connectors and IO, thermal/high-power spacing. Say explicitly how each idea stays
**deterministic and explainable**.

**D. Measurement.** HPWL alone clearly does not capture "a senior engineer would
approve." Propose metrics, heuristics, or evaluation methods (including how to
A/B or score candidates) that actually correlate with good placement.

**E. Phased roadmap.** What to do first for the biggest quality jump per unit
effort, and what is longer-term research.

## Constraints to respect (or argue against, from PCB-engineering reality)

- Keep the **pure-Python / KiCad-independent** engine boundary, or justify changing it.
- **Determinism per seed** is valued. "Less random" must not mean "more opaque."
- Placement only **translates + rotates**; it must never move **locked** parts; **connectors** are pinned to board edges.
- It already does: force-directed seeding, per-sheet floorplanning, SA with a quality metric, route-driven refinement, and GND/power plane handling — build on these, don't ignore them.
- Be candid about trade-offs and where heuristics conflict. Disagreement with the current design is welcome if argued well.

## Output format

Start with a 5–10 line executive summary of your top recommendations, then the
sections above. Reference concrete file paths and symbols. Favor specific,
implementable proposals over generalities. Assume the reader knows both software
and PCB design.
