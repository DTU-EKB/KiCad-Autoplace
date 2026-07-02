"""Measure tall_clearance with the halo ON vs OFF (placement only). KiCad python.
  python tall_measure.py <board.kicad_pcb>
"""
import copy
import sys

sys.path.insert(0, "plugin/plugins")

from autoplace import engine, fabrication, kicad_io, metrics  # noqa: E402

src = sys.argv[1]
model, _ = kicad_io.load_board(src)
M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")
n_tall = sum(1 for c in model.components.values() if c.height >= metrics.TALL_MM)

# halo ON: place with real heights
on = copy.deepcopy(model)
engine.place(on, seed=0, margin=M, track=T)
tc_on = metrics.tall_clearance(on, M, T)

# halo OFF: place with heights forced low (no halo fires), then restore true
# heights so the metric measures the same tall parts on the un-haloed layout.
off = copy.deepcopy(model)
true_h = {r: c.height for r, c in off.components.items()}
for c in off.components.values():
    c.height = 4.0
engine.place(off, seed=0, margin=M, track=T)
for r, c in off.components.items():
    c.height = true_h[r]
tc_off = metrics.tall_clearance(off, M, T)

print(f"board={src.rsplit('/', 1)[-1]} tall_parts={n_tall}")
print(f"tall_clearance ON  (halo): {tc_on} mm")
print(f"tall_clearance OFF (halo): {tc_off} mm")
print("RESULT:", "IMPROVED" if tc_on < tc_off else "NOT IMPROVED",
      f"({round(tc_off - tc_on, 2)} mm less intrusion)")
