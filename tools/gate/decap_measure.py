"""Measure decap_proximity with the term ON (DECAP=1.5) vs OFF (DECAP=0).
Placement only (no routing). Run under KiCad 10 python.
  python decap_measure.py <board.kicad_pcb>
"""
import copy
import sys

sys.path.insert(0, "plugin/plugins")

from autoplace import electrical, engine, fabrication, kicad_io, metrics  # noqa: E402
from autoplace.anneal import _Weights  # noqa: E402

src = sys.argv[1]
model, _ = kicad_io.load_board(src)
n_pairs = len(electrical.decoupling_pairs(model))

M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")

on = copy.deepcopy(model)
engine.place(on, seed=0, margin=M, track=T)
prox_on = metrics.decap_proximity(on)

_Weights.DECAP = 0.0                      # disable the term
off = copy.deepcopy(model)
engine.place(off, seed=0, margin=M, track=T)
prox_off = metrics.decap_proximity(off)

print(f"board={src.rsplit('/', 1)[-1]} decap_pairs={n_pairs}")
print(f"decap_proximity ON  (DECAP=1.5): {prox_on} mm")
print(f"decap_proximity OFF (DECAP=0.0): {prox_off} mm")
print("RESULT:", "IMPROVED" if prox_on < prox_off else "NOT IMPROVED",
      f"({round(prox_off - prox_on, 2)} mm closer)" if n_pairs else "(no decaps on this board)")
