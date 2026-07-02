"""Placement-level check of aesthetic v2: place with aesthetic ON, report tidiness +
legality. KiCad python.  python spacing_check.py <board1> [<board2> ...]
"""
import sys
sys.path.insert(0, "plugin/plugins")
from autoplace import engine, fabrication, kicad_io, metrics  # noqa: E402

M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")
print(f"{'board':20s} {'align':>7s} {'uneven':>7s} {'aligned':>7s} {'spaced':>6s} {'overlaps':>8s}")
for src in sys.argv[1:]:
    name = src.replace("\\", "/").rsplit("/", 1)[-1]
    model, _ = kicad_io.load_board(src)
    rep = engine.place(model, seed=0, margin=M, track=T)
    al = metrics.alignment_score(model)
    un = metrics.spacing_unevenness(model)
    nov = len(metrics.overlaps(model))
    print(f"{name:20s} {al:>7.3f} {un:>7.3f} {rep.get('aligned_parts',0):>7d} "
          f"{rep.get('spaced_parts',0):>6d} {nov:>8d}  {'OK' if nov==0 else 'OVERLAP!'}")
