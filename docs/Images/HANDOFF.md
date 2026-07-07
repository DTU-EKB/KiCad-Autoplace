# KiCad-Autoplace — Portfolio Image Handoff

**Audience:** the AI agent maintaining madsrudolph.dev.
**Purpose:** embed this project's screenshots as a case study. Images live in this folder
(`docs/Images/`) alongside this file — use relative paths from here.

**The story arc, in order:** the app takes a raw unplaced board, generates and scores placement
candidates, refines the winner, and hands back a routable board — which was then actually
CNC-milled. Suggested section order below follows that arc; don't reorder without reason.

---

## 1. `DTU-EKB_GUI.png`
**Hero shot.** Full app window: sidebar (target board selected, Advanced Settings expanded —
strategy/fabrication/routing/refine-effort dropdowns visible), pre-run check all green (131
footprints, ground net, copper pours found), and the board canvas showing the board **before**
placement — footprints color-coded by circuit sub-block (buck, boost, MPPT, motor driver, etc.),
still in their raw/scattered import positions.
- **Use as:** the top-of-case-study image, first thing a visitor sees.
- **Caption idea:** "Desktop app for automated PCB placement — load a board, tune strategy, run."

## 2. `Generated_candidates.png`
Candidate gallery: 6 placement seeds generated in parallel, each with wirelength (HPWL),
% reduction vs. baseline, net-crossing count, and overlap count. Seed 1 is auto-recommended
(lowest crossings at competitive wirelength).
- **Use as:** "how it works" step 2 — shows the engine explores multiple layouts and scores them,
  not just a single greedy pass.
- **Caption idea:** "Multiple candidate layouts are generated and scored automatically."

## 3. `Refining_in_progress.png`
Small progress-bar crop: "Refining — iteration 2/10", 20%, live routed estimate 97.8%.
- **Use as:** a small inline/inset image next to the candidates or results section, not a
  standalone hero — it's a supporting detail, not a full scene.
- **Caption idea:** "Iterative refinement tracks routability in real time."

## 4. `after_run_Autoplacement.png`
The strongest metrics shot. Shows the post-run candidate gallery (seed 0 now recommended, 92.7%
routed) plus the full **Results** dashboard below it: wirelength 2,878mm (‑33.6%), net crossings
164 (‑263 vs. baseline 427), overlaps 0 (after legalize), 131 components across 6 blocks.
- **Use as:** the "proof it works" image — pair with a callout of the headline numbers
  (‑33.6% wirelength, 0 overlaps) if the site does stat callouts.
- **Caption idea:** "Final placement: 33.6% shorter wirelength, zero overlaps, fully legalized."

## 5. `after_refining.png`
The app's own board canvas after refinement, now showing routed copper (F.Cu/B.Cu overlay) at
97.8% routed — this is Freerouting's output rendered back inside the app, not KiCad.
- **Use as:** bridges "placement" to "it's actually routable" before switching to real KiCad shots.
- **Caption idea:** "Routed preview inside the app — 97.8% auto-routed."

## 6. `System_PCB_Before_Kicad_Autoplace.png`
Real KiCad PCB editor, the **raw imported board**: footprints unplaced/overlapping, ratsnest
lines crossing everywhere. This is the actual problem the tool solves, shown in the tool
everyone recognizes (KiCad), not the app's own UI.
- **Use as:** "before" half of a before/after pair (with #8 or #9).
- **Caption idea:** "Before: raw schematic import, no placement."

## 7. `final_refine_routed_Kicad_demo.png`
Real KiCad PCB editor, final routed board from **a demo run** of the pipeline (candidate → refine
→ route). Note: this is a different run than the board that was physically fabricated (#8/#9) —
same design, not guaranteed to be pixel-identical layout. Don't caption it as "the board that was
built"; it's a demonstration of the pipeline's output quality in KiCad.
- **Use as:** supporting "the tool produces clean, routed boards in standard KiCad" shot.
- **Caption idea:** "Auto-placed and routed, opened in KiCad for review."

## 8. `System_PCB_After_Kicad_Autoplace&Freerouting_ProducedBoard.png`
Real KiCad PCB editor, the **exact layout that was fabricated** — this is the design file behind
the physical board in #9/#10. Copper pours and full routing visible.
- **Use as:** "after" half of the before/after pair with #6, and the direct lead-in to the physical
  photos.
- **Caption idea:** "After: fully placed and routed — this design was fabricated."

## 9. `pcb_fcu_produced.png` and 10. `pcb_bcu_produced.png`
**Physical photos** of the actual bare, milled/etched board — front (F.Cu) and back (B.Cu) copper
sides, no components populated yet. This is the tangible real-world result of the whole pipeline,
not a render.
- **Use as:** the closing pair of the case study — the payoff after software → KiCad → fabrication.
  Show side by side (front/back) if the layout allows.
- **Caption idea:** "The result: a real, CNC-milled board — front and back."

---

## Notes for whoever builds the page
- #6 → #8 → (#9, #10) reads as one continuous before → after → real-object arc; keep them
  visually adjacent even if other images are interspersed elsewhere.
- #7 is easy to confuse with #8 — they look similar but are different fabrication runs. Caption
  them distinctly (see above) so it doesn't read as a duplicate image to a careful viewer.
- All images are dark-theme (app UI and KiCad are both dark); the physical board photos (#9, #10)
  are the only ones with a light/neutral background — good candidates for a section break or
  background-color transition if the page alternates light/dark blocks.
