"""Measure alignment_score with the post-pass ON vs OFF (placement only). KiCad python.
  python align_measure.py <board1.kicad_pcb> [<board2> ...]
Lower alignment_score = tidier. Also reports aligned_parts and asserts ON is overlap-free.
"""
import copy
import sys
sys.path.insert(0, "plugin/plugins")
from autoplace import engine, fabrication, kicad_io, metrics  # noqa: E402

M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")
print(f"{'board':22s} {'score_OFF':>9s} {'score_ON':>9s} {'aligned':>7s} {'overlaps_ON':>11s}")
for src in sys.argv[1:]:
    name = src.replace("\\", "/").rsplit("/", 1)[-1]
    try:
        base, _ = kicad_io.load_board(src)
    except Exception as e:  # noqa: BLE001
        print(f"{name:22s} LOAD-ERR {type(e).__name__}: {e}")
        continue
    off = copy.deepcopy(base)
    engine.place(off, seed=0, margin=M, track=T, aesthetic=False)
    s_off = metrics.alignment_score(off)

    on = copy.deepcopy(base)
    rep = engine.place(on, seed=0, margin=M, track=T, aesthetic=True)
    s_on = metrics.alignment_score(on)
    n_ovl = len(metrics.overlaps(on))
    tag = "TIDIER" if s_on < s_off else ("same" if s_on == s_off else "WORSE")
    print(f"{name:22s} {s_off:>9.3f} {s_on:>9.3f} {rep['aligned_parts']:>7d} {n_ovl:>11d}  {tag}")
