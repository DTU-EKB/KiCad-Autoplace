"""Sweep _Weights.DECAP and measure decap_proximity (placement-only, no routing).
KiCad python.  python decap_sweep.py <board1.kicad_pcb> [<board2> ...]
Prints, per board, mean decap_proximity (mm; lower=better) at each DECAP weight.
"""
import copy
import sys
sys.path.insert(0, "plugin/plugins")
from autoplace import electrical, engine, fabrication, kicad_io, metrics  # noqa: E402
from autoplace import anneal  # noqa: E402

WEIGHTS = [0.0, 1.5, 3.0, 5.0, 8.0]
M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")

print(f"{'board':22s} {'pairs':>5s} " + " ".join(f"D={w:<5g}" for w in WEIGHTS))
for src in sys.argv[1:]:
    name = src.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        base, _ = kicad_io.load_board(src)
    except Exception as e:  # noqa: BLE001
        print(f"{name:22s} LOAD-ERR {type(e).__name__}: {e}")
        continue
    n_pairs = len(electrical.decoupling_pairs(base))
    if not n_pairs:
        print(f"{name:22s} {0:>5d} (no decaps)")
        continue
    cells = []
    for w in WEIGHTS:
        anneal._Weights.DECAP = w
        b = copy.deepcopy(base)
        engine.place(b, seed=0, margin=M, track=T)
        cells.append(f"{metrics.decap_proximity(b):<7.2f}")
    print(f"{name:22s} {n_pairs:>5d} " + " ".join(cells))
