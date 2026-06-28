# PCB Autoplace — Build Specification

An open, KiCad-focused equivalent of [AutoCuro](https://autocuro.com/): automated
schematic-aware **component placement + routing** that runs locally and outputs a
native `.kicad_pcb` file.

Status: implemented through M4 (block detection + SA placement) — see §7 for results
Target EDA: **KiCad 9** only (KiCad-10 board format support is M6)
Platform: Windows (primary), Linux (CI)
Repo: https://github.com/DTU-EKB/KiCad-Autoplace

---

## 1. Goal & scope

**The product is automated PCB *placement*.** The existing DTU workflow already generates
schematics well and already routes well (two-stage FreeRouting, see §1.5) — the one part
that "sucks" is placement, which today is **hand-typed XY coordinate dicts** per board. The
ultimate goal of this tool:

> Given a netlist + board outline + a few locked parts, automatically produce a
> **connectivity-aware, overlap-free, single-sided-routable placement** — no hardcoded
> per-board coordinates — that the existing FreeRouting flow can finish to ~100%.

Routing, DRC, and report are supporting cast that we **reuse** from the DTU repo. Placement
is where all the new engineering goes.

### Why the current placement is the problem (evidence from the code)
1. **Hardcoded coordinates.** `pcb_build.py` carries a `PLACE = {...}` dict of absolute XY
   for `buck_v2`, `current_sense`, `mppt`, `c2000_feedback`; `place_system3.py` carries
   `EDGE`/`REGION` dicts. Every serious board is placed by hand. This is the labor the tool
   must eliminate.
2. **No connectivity awareness.** The non-hardcoded fallback packs parts into rows sorted by
   *refdes class* (`r[0] in "DLQU"`), alphabetically — parts that are *wired together* are
   not placed together. The code's own comment: grid auto-placement "spreder kanal-
   komponenterne og sulter routeren" (spreads the channel components and starves the router).
3. **No routability objective.** Nothing minimizes wirelength or net crossings, so
   single-sided routing is hard *as a direct consequence of the placement*.

### In scope (MVP) — placement-centric
- Parse `.kicad_sch` for connectivity, hierarchy, net classes (feeds placement).
- **Connectivity-aware auto-placement engine** (§4 is the heart of this spec): block
  detection → floorplan → force-directed seed → simulated-annealing refine → legalize/snap,
  optimizing wirelength + **net-crossing (single-sided routability)** + block cohesion +
  decap-near-IC + connector-to-edge, respecting locked parts and the board outline.
- **Reuse** the proven routing (`pcb_route.py` two-stage flow), DRC, and report as-is.
- Ship as a **KiCad Action Plugin** (toolbar button) plus a headless CLI.
- Runs 100% locally; design files never leave the machine.

### Out of scope (MVP)
- **New routing work.** Routing is already solved; we wrap the existing flow unchanged.
- Altium / Cadence support (separate adapters later).
- Cloud service, licensing server, multi-tenant accounts.
- Full signal-integrity simulation / field-solver impedance.
- Thermal / DFM optimization beyond basic clearance rules.

### Non-goals to be honest about
AutoCuro markets "AI" but publishes no algorithm details. We will **not** pretend to a
trained model we don't have. The "intelligence" is a deterministic, heuristic
intent-extraction + constrained-optimization pipeline. An optional ML reranker is a
clearly-marked future phase, not the MVP.

---

## 1.5 Existing foundation — the DTU Energy Systems repo

We are **not** starting from zero. The repo at
`C:\Users\Mads2\DTU\4. Semester\Electrical Energy Systems\team\hardware\kicad`
already implements a working, board-specific version of this exact pipeline (KiCad 9,
two-stage FreeRouting, DRC + render). The project's real job is to **extract, generalize,
and productize** that proven code — not reinvent it.

### What already exists (maps directly onto AutoCuro's pipeline)
| AutoCuro stage | Existing asset in the DTU repo | State |
|---|---|---|
| Schematic → netlist | `kicad-cli sch export netlist` + `tools/pcb_netlist_json.py` | ✅ works |
| Netlist → placement | `tools/pcb_build.py` (grid + connectors-on-edges), `place_system{,2,3}.py` (justified, connectivity-aware) | ⚠️ board-specific, hardcoded |
| Placement → routing | `tools/pcb_route.py` + `tools/pcb_make_all.ps1` (two-stage FreeRouting 1.9.0, F.Cu layer-masking, lock-and-finish) | ✅ works, clever |
| DRC + render | `kicad-cli pcb drc` / `pcb render` (top/bottom PNG) | ✅ works |
| BOM report | `tools/bom_to_md.py` | ✅ works |
| Net validation | `tools/netcheck.py` | ✅ works |
| Production export | `tools/pcb_export_production.ps1` (DXF + Gerbers) | ✅ works |
| Design rules | per-board `.kicad_pro` (track 1.0 mm, clearance 0.8 mm — fiber-laser) | ✅ works |

The two-stage routing trick in `pcb_make_all.ps1` is genuinely the AutoCuro "intent" idea
in embryo: route the etch side first with F.Cu masked as a power layer, then **lock that
copper (`type fix`)** and let FreeRouting finish the rest with the top allowed. That
priority ordering is exactly what we formalize in §4.5.

### What's missing vs. AutoCuro (= the actual new work)
1. **Schematic-intent extraction.** Current placement uses **hardcoded** per-board edge
   coordinates (e.g. the `EDGE = {...}` dict in `place_system3.py`) and ref-class grids.
   There is **no** automatic power-net / diff-pair / functional-block detection. This is
   the AutoCuro differentiator and the bulk of the new effort (§4.1).
2. **Generalization.** Everything is board-specific: hardcoded absolute paths, per-board
   placement dicts, fixed board dimensions (`BW, BH = 200, 130`). Must become
   config-driven and board-agnostic.
3. **Optimization.** Placement is deterministic grid/heuristic, not cost-minimizing.
   No wirelength objective, no annealing (§4.4).
4. **Packaging.** PowerShell orchestration → installable KiCad plugin + clean CLI.
5. **Unified report.** Today: a DRC `.txt` + two PNGs. AutoCuro-style: one HTML report.

### Test-fixture suite (real boards, free)
The repo ships 13 boards spanning the full complexity range — an ideal regression suite:

| Board | Footprints | Role |
|---|---|---|
| `rectifier` | 12 | small / smoke test |
| `feedback_circuit`, `motor_feedback` | 10 | small analog |
| `boost`, `buck` | 11 | converter (power + magnetics) |
| `drive_circuit` | 14 | gate drive |
| `current_sense` | 17 | mixed-signal |
| `mppt`, `mppt_buck` | 20–21 | converter + control |
| `c2000_feedback` | 47 | MCU-class, mid complexity |
| `motor_power` | 58 | high-power, dense |
| `system` | 131 | hierarchical top-level — the flagship stress test |

These replace the generic "golden boards" in §8.

## 2. Architecture overview

```
                ┌──────────────────────────────────────────────┐
                │            PCB Autoplace pipeline             │
                │                                              │
 .kicad_sch ───▶│ 1. Schematic parser  ─▶  Design-Intent Model │
 .kicad_pcb ───▶│ 2. Board/outline loader                      │
 rules.yaml ───▶│ 3. Constraint loader                         │
                │                                              │
                │ ╔══════════════════════════════════════════╗ │
                │ ║ 4. PLACEMENT ENGINE  ◀── all new work     ║ │
                │ ║    floorplan → force-directed → SA → snap ║ │
                │ ╚══════════════════════════════════════════╝ │
                │ 5. Router        (reuse DTU FreeRouting flow) │
                │ 6. DRC + report  (reuse kicad-cli + renders)  │
                └──────────────────────────────────────────────┘
                                  │
                       outputs:  .kicad_pcb (placed [+ routed])
                                 report.html, run.json
```

The dashed box is the product. Stages 1–3 feed it; 5–6 are lifted from the DTU repo.

Two front ends, one core:
- **KiCad Action Plugin** — button in `pcbnew`; thin UI over the core.
- **CLI** — `pcb-autoplace run --sch board.kicad_sch --pcb board.kicad_pcb --rules rules.yaml`.

The core never imports `pcbnew` directly for *logic*; it goes through an adapter
(`kicad_io`) so the core stays testable without a running KiCad.

---

## 3. Technology stack

| Concern | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Matches KiCad's bundled Python; you already run `py -3.13`/3.11 envs |
| `.kicad_pcb` / `.kicad_sch` read/write | **kiutils** + `sexpdata` fallback | Pure-Python S-expr parsing; no KiCad runtime needed for parsing |
| Board mutation inside KiCad | **`pcbnew` Python API** | Place footprints, push tracks/vias, run DRC |
| Placement optimization | `scipy.optimize` (dual annealing) + custom force-directed seed | Start simple, deterministic seed via fixed RNG |
| Routing | **FreeRouting** (Java, headless `-de`/`-do`) | KiCad already speaks Specctra DSN ↔ SES |
| Graph / netlist | `networkx` | Connectivity graph, block detection |
| Reports | `jinja2` → HTML, optional `weasyprint` → PDF | Self-contained, offline |
| Config | `pydantic` + YAML | Validated rules file |
| CLI | `typer` | |
| Packaging | KiCad PCM plugin (zip + `metadata.json`) | Installable via KiCad Plugin & Content Manager |
| Tests | `pytest` + sample boards | Core logic mocked away from KiCad |

Everything above is open-source and runs offline.

---

## 4. Pipeline stages (detail)

### 4.1 Schematic parser → Design-Intent Model
Parse `.kicad_sch` (and hierarchical sub-sheets) to extract:
- Components (ref, value, footprint, sheet path).
- Nets and pin connectivity.
- **Net classes** already defined in the project.
- **Naming-convention inference:**
  - Power/ground: `+3V3`, `+5V`, `VCC`, `VBAT`, `GND`, `AGND`, `VDD*`.
  - Differential pairs: suffixes `_P/_N`, `+/-`, `D+/D-`, `*_DP/_DN`, USB/Ethernet/LVDS patterns.
  - Buses / high-speed: `*_CLK`, `SPI*`, `SDIO*`, `RGMII*`, named by interface.
- **Functional-block detection:** cluster components by sheet, by shared local nets, and
  by decoupling relationships (a cap whose only nets are a power rail + GND near an IC →
  belongs to that IC's block). Use `networkx` community detection on the net graph as a
  starting heuristic.

Output: a serializable `DesignIntent` object (also dumped to `run.json` for debugging).

### 4.2 Board / outline loader
- Read the `Edge.Cuts` outline, existing footprints, locked items, keepouts, stackup,
  and any **pre-placed (locked) components** — these are hard constraints.

### 4.3 Constraint loader (`rules.yaml`)
A single validated file (see §6) for track widths, clearances, layer count/stackup,
diff-pair impedance targets, via rules, and placement hints (edge connectors, keep-outs,
component grouping overrides).

### 4.4 Placement engine — THE CORE (this is the whole product)

This is the section that matters. It replaces every hardcoded `PLACE`/`EDGE`/`REGION` dict
with a deterministic, connectivity-aware optimizer. Modeled on how real EDA placers work
(global placement → legalization → detailed placement), tuned for these boards:
THT, single-sided / 2-layer, block-structured, ≤131 parts.

#### Inputs
- Connectivity graph (components as nodes, nets as hyperedges) from §4.1.
- Footprint geometry: courtyard bbox, pad locations (pin positions matter — wirelength is
  pin-to-pin, not center-to-center).
- Board outline + keepouts (§4.2); **locked/pre-placed parts = hard pins** (never moved).
- Design-intent: functional blocks, power/GND nets, connector list (§4.1).
- Rules: placement grid, clearance, allowed orientations (§4.3).

#### Cost function `f(placement)`
Weighted sum (weights in `rules.yaml`, with per-board-class presets):
| Term | What | Type |
|---|---|---|
| **Wirelength** | Σ HPWL over nets, computed on **pad** positions | soft (primary) |
| **Crossings** | estimated net-crossing count — the single-sided-routability proxy | soft (primary) |
| Overlap | courtyard intersection area | **hard** (must be 0) |
| Off-board / keepout | any part outside outline or in a keepout | **hard** |
| Block cohesion | spread (bbox area) of each functional block's members | soft |
| Decap proximity | distance from each decoupling cap to its IC's power pin | soft |
| Connector edge-affinity | distance of connectors to the nearest board edge | soft |
| Power-net spine | keep high-current net (e.g. `+5V`, `GND`) members compact/aligned | soft |
| Grid/orientation | snapped to grid, orientation ∈ {0,90,180,270} | post-step |

**The crossing term is the key insight** the current code lacks: a placement that's close to
*planar* (few net crossings) is one FreeRouting can finish on a single layer. We minimize
crossings explicitly rather than discovering routability problems after the fact.

#### Algorithm (3 phases)
1. **Block floorplan.** Detect blocks (§4.1: sheet hierarchy + graph community detection),
   then arrange block rectangles on the outline — connectors' blocks pulled to edges by
   their external I/O, blocks placed to minimize inter-block wirelength. This auto-derives
   what `REGION = {...}` is hand-drawn today.
2. **Global placement (per block + global).** Force-directed: nets = attractive springs
   (stiffness ∝ 1/net-degree so fat power nets don't collapse everything), courtyards =
   repulsive. Converges to a connectivity-respecting continuous layout. Deterministic
   (fixed RNG seed). This is the principled version of the row-packing in `place_system3`.
3. **Legalize + detailed refine.** Snap to grid, resolve overlaps (shelf/row legalizer),
   then **simulated annealing** with moves {swap two parts, nudge, rotate 90°, reflect}
   minimizing the full cost with overlap as a hard reject. SA is the workhorse at this
   scale and absorbs the messy multi-objective cost cleanly.

#### Outputs & guarantees
- Mutates footprint positions/orientations in the board; writes `.kicad_pcb`.
- Locked parts byte-identical. Zero overlaps (asserted, like `place_system3`'s overlap
  check). All parts inside outline. Deterministic given the same inputs + seed.
- Emits placement metrics (HPWL, est. crossings, block spread) to `run.json` for §8.

#### Why not just KiCad's built-in / ML?
KiCad 9 has no real autoplacer (only "pack & move"). A trained ML placer needs a dataset we
don't have. Force-directed + SA is the proven, explainable, dataset-free approach and is a
direct evolution of the heuristics already in the repo — so it's the MVP. An optional ML
reranker over SA candidates is a labeled future phase, not a claim we make now.

### 4.5 Routing — REUSED AS-IS (not new work)
The DTU two-stage FreeRouting flow already routes these boards well; we **wrap it
unchanged** behind a clean module:
- `pcb_route.py` modes (`dsn` / `sesraw` / `lockdsn` / `ses`) → a `router/` API.
- The `pcb_make_all.ps1` trick (route B.Cu with F.Cu masked as power → lock that copper
  `type fix` → let FreeRouting finish) → encoded as the two-pass default.
- Net-class rules (track 1.0 mm, clearance 0.8 mm) already live in the generated
  `.kicad_pro`.
No diff-pair/impedance routing in MVP — these power boards don't need it; revisit if a
high-speed board appears.

### 4.6 DRC + report — REUSED
- DRC via `kicad-cli pcb drc` (already in the pipeline).
- Report: fold the existing top/bottom renders + DRC text + `bom_to_md.py` output into one
  HTML page, plus the new **placement metrics** (before/after thumbnails, HPWL, est.
  crossings, overlap count = 0, routing completion %).

---

## 5. Public interfaces

### CLI
```bash
pcb-autoplace run \
  --sch    board.kicad_sch \
  --pcb    board.kicad_pcb \
  --rules  rules.yaml \
  --out    board.out.kicad_pcb \
  --report report.html \
  [--place-only] [--route-only] [--seed 42]
```

### KiCad Action Plugin
- Toolbar button → dialog: pick rules file, choose place / route / both, show progress,
  open report on completion. Operates on the currently open board.

---

## 6. `rules.yaml` schema (sketch)

```yaml
board:
  layers: 4
  stackup: JLCPCB_4L_1.6mm        # named preset → trace geometry
clearances:
  default_mm: 0.15
  high_voltage_mm: 0.5
tracks:
  default_width_mm: 0.20
  power_width_mm: 0.50
vias:
  default: { drill_mm: 0.3, diameter_mm: 0.6 }
diff_pairs:
  impedance_ohm: 90              # used to derive width/gap from stackup
  length_match_tol_mm: 0.5
placement:
  grid_mm: 0.5
  edge_connectors: [J1, J2]      # force to board edge
  keepouts:
    - { layer: F.Cu, rect_mm: [10, 10, 20, 20] }
  groups:                        # override auto block detection
    - { name: buck, refs: [U2, L1, C10, C11, D1] }
net_overrides:
  high_speed: [SPI1_SCK, SDIO_CLK]
```

---

## 7. Milestones

Reframed around the existing DTU pipeline: **harvest first, then generalize, then add the
intent layer.** Effort drops where we're lifting proven code (`pcb_route.py`,
`pcb_make_all.ps1`, `pcb_netlist_json.py`) rather than writing it.

Front-loaded on placement. Routing/DRC/report are ports (cheap); the weeks go into §4.4.

| # | Milestone | Deliverable | Status |
|---|---|---|---|
| M0 | Repo + skeleton + fixtures | Package, CLI, plugin, headless tests; connectivity graph from `pcbnew` | ✅ done |
| M1 | Reuse routing + report | (deferred — routing reuse not yet wired; placement prioritised) | ⬜ todo |
| M2 | **Placement v1 — connectivity-aware** | Force-directed global + grid legalize + overlap=0 | ✅ done |
| M3 | Functional-block detection | Label-propagation on signal nets (`blocks.py`); replaces `REGION`/`EDGE` dicts. *(.kicad_sch hierarchy seeding still todo)* | ✅ core done |
| M4 | **Placement v2 — SA refine** | `anneal.py`: incremental SA (nudge/rotate/swap), cost = HPWL + hard overlap + connector-edge + block cohesion + density-adaptive routing-channel | ✅ done |
| M4b | Rotation + channel + scaling | 0/90/180/270 rotation moves (pcbnew-verified matrix), channel/spread term relaxed by board density, SA effort scales with part count | ✅ done |
| M5 | KiCad plugin packaging | PCM-installable zip + filled `packages.json` (sha256/sizes) + GitHub release | ✅ done |
| M6 | Validation + weight tuning | Regression over all 12 boards; **KiCad-9 and 10 both load** (engine runs inside whichever KiCad launches it); per-board-class presets | ⬜ ongoing |

### Measured results (full board suite, final engine vs. hand-placement)
HPWL excludes power nets; crossings = intersecting MST edges; overlaps always 0.
| Board | Parts | Blocks | HPWL Δ | Crossings (hand → ours) |
|---|---|---|---|---|
| feedback_circuit | 10 | 3 | **−69%** | 22 → **1** |
| motor_feedback | 10 | 2 | **−61%** | 11 → **1** |
| current_sense | 17 | 6 | **−56%** | 10 → **3** |
| mppt | 21 | 7 | **−56%** | 17 → **6** |
| rectifier | 12 | 6 | **−50%** | 11 → **1** |
| c2000_feedback | 47 | 14 | **−39%** | 9 → **8** |
| system | 131 | 38 | **−36%** | 438 → **272** |
| buck | 11 | 4 | **−33%** | 0 → 2 |
| drive_circuit | 14 | 5 | +14% | 3 → 8 |
| mppt_buck | 20 | 8 | +16% | 9 → 10 |
| boost | 11 | 5 | +27% | 0 → 1 |
| motor_power | 58 | 18 | +91% | 26 → 145 |

**8 of 12 boards beat hand-placement on wirelength, most with fewer crossings too**, and the
131-part `system` board improved −36% / 438→272 crossings. All overlap-free and
deterministic. Known weak cases: tiny tightly-hand-packed boards (`boost`, `drive_circuit`,
`mppt_buck`, +14–27%) and the dense-but-spacious `motor_power` (35% utilisation, 18 blocks
half of them singletons — sprawls without a true block floorplan). A connectivity-aware
floorplanner was prototyped but *hurt* the mid boards (rigid shelf rows), so it was reverted;
a better floorplan that doesn't disturb the working boards is the main open item, along with
per-board-class weight presets.

**Delivered (M0–M5):** automatic, connectivity- and block-aware, rotation-capable placement
with zero hardcoded coordinates, overlap-free and deterministic, running on KiCad 9 and 10,
beating hand-placement on most boards, packaged as a PCM-installable plugin.

---

## 8. Validation plan
- **Golden boards = the 13 DTU Energy Systems boards** (see §1.5 table), spanning 10→131
  footprints. They already have known-good routed/produced outputs in `production/`, so we
  can diff our results against a human-blessed baseline rather than guessing.
- Smoke tier: `rectifier`, `buck`, `boost` (fast, must stay green in CI).
- Stress tier: `motor_power` (58, dense power) and `system` (131, hierarchical).
- Metrics per run: placement HPWL, routing completion %, DRC violation count, diff-pair
  match error, wall-clock time. Track these across cost-weight changes to avoid regressions.
- Baseline to beat: the current `pcb_make_all.ps1` output (target ≥ its completion %, ≤ its
  DRC violations, with *zero* hardcoded per-board placement data).

---

## 9. Honest risk register

| Risk | Reality | Mitigation |
|---|---|---|
| **Placement quality is the hard part** | Multi-objective SA tuning is fiddly; "good" is subjective | Strong force-directed seed; per-board-class weight presets; locked-part workflow keeps a human in control; measure against the hand-placed baseline |
| Crossing/routability proxy may mispredict | HPWL+crossings ≠ true congestion | Validate by actually routing each candidate in M4; fall back to running the router as the final judge |
| SA non-determinism / slow on `system` (131) | Annealing can be slow & jittery | Fixed RNG seed; time-boxed schedule; hierarchical (per-block then global) to cut the search space |
| `pcbnew` API churn across KiCad versions | Breaks plugins | Pin to KiCad 9 API; isolate in `kicad_io` adapter |
| Beating a human's hand-placement | The current dicts are actually decent | Goal is *parity with zero manual labor*, then exceed; ship when it matches hand-placed routability |
| Footprint anchor/centering quirks | `pcb_build.py` already fights bbox-vs-pad1 anchoring | Reuse its proven `put()` centering; place by pad centroid |

---

## 10. Differentiation — why this beats the status quo
What makes this a real placement tool rather than the current hand-typed dicts:
1. **Zero hardcoded coordinates** — no `PLACE`/`EDGE`/`REGION` dict per board.
2. **Connectivity-driven** — wired-together parts placed together (today: refdes-alphabetical).
3. **Routability as an objective** — minimizes net crossings so single-sided routing works,
   instead of discovering congestion after routing fails.
4. **Auto functional blocks** — derived from schematic hierarchy, not hand-drawn regions.
5. **Connector-to-edge + decap-near-IC** automatically.
6. **Locked-part-aware** human-in-the-loop: pin the few parts you care about, optimize the rest.
7. **Drops into the existing route/DRC/production flow** unchanged.
