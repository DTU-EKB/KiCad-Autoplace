"""Is engine.place deterministic? Place the same board N times in ONE process and
fingerprint the geometry. Stable digest (sha1 of rounded coords), hashseed-independent.
  python determinism_probe.py <board1> [<board2> ...]
"""
import copy
import hashlib
import sys
sys.path.insert(0, "plugin/plugins")
from autoplace import engine, fabrication, kicad_io  # noqa: E402

M, T = fabrication.margin_for("cnc"), fabrication.track_for("cnc")

def fp(board):
    s = ";".join(f"{c.ref},{round(c.x,3)},{round(c.y,3)},{c.rot}"
                 for c in sorted(board.components.values(), key=lambda c: c.ref))
    return hashlib.sha1(s.encode()).hexdigest()[:12]

for src in sys.argv[1:]:
    name = src.replace("\\", "/").rsplit("/", 1)[-1]
    base, _ = kicad_io.load_board(src)
    digests_off, digests_on = [], []
    for _ in range(5):
        b = copy.deepcopy(base)
        engine.place(b, seed=0, margin=M, track=T, aesthetic=False)
        digests_off.append(fp(b))
        b2 = copy.deepcopy(base)
        engine.place(b2, seed=0, margin=M, track=T, aesthetic=True)
        digests_on.append(fp(b2))
    uoff, uon = set(digests_off), set(digests_on)
    print(f"{name:22s} aesthetic=OFF: {'STABLE' if len(uoff)==1 else f'VARIES({len(uoff)})'} {sorted(uoff)}")
    print(f"{'':22s} aesthetic=ON : {'STABLE' if len(uon)==1 else f'VARIES({len(uon)})'} {sorted(uon)}")
