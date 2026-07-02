"""Cross-process determinism: seed-0 placement must not depend on PYTHONHASHSEED.

Hash randomization changes str-set iteration order between processes; if any such
order leaks into the placement math (e.g. float summation order in the annealer's
local_cost), the same board + seed places DIFFERENTLY in different processes --
which silently breaks gate comparability and the app's reproducibility contract
(invariant: sort before iterating sets/dicts where the result depends on order).
Caught live 2026-07-02: motor_power flipped between two stable layouts with
~25/75 odds per process. This test replays a small dense board in subprocesses
under several hash seeds and requires identical geometry digests.
"""
import os
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_SCRIPT = r"""
import hashlib, os, sys
sys.path.insert(0, os.path.join(r"%s", "plugin", "plugins"))
from autoplace import engine
from autoplace.model import Board, Component, Pad

def part(ref, x, y, nets, w=2.6, h=1.3):
    pads = [Pad(str(i + 1), n, -w / 2 + 0.3 + i * 0.7, (i %% 2) * 0.4 - 0.2)
            for i, n in enumerate(nets)]
    return Component(ref=ref, w=w, h=h, x=x, y=y, pads=pads)

b = Board(0, 0, 42.7, 33.1)
names = (["SIG_%%d" %% i for i in range(9)]
         + ["CTRL_A", "CTRL_B", "SENSE_X", "CLK", "MISO", "MOSI"])
comps = {}
for k in range(14):
    ref = "U%%d" %% k
    nets = [names[(k * 3 + j) %% len(names)] for j in range(3)]
    comps[ref] = part(ref, 3.1 + (k %% 5) * 7.7, 4.3 + (k // 5) * 9.1, nets)
b.components = comps
engine.place(b, seed=0, sa_steps=4000)
s = ";".join("%%s,%%.4f,%%.4f,%%d" %% (c.ref, c.x, c.y, c.rot)
             for c in sorted(b.components.values(), key=lambda c: c.ref))
print(hashlib.sha1(s.encode()).hexdigest())
""" % REPO


def test_placement_is_hashseed_independent():
    digests = {}
    for hs in ("1", "2", "3", "4", "5", "6"):
        env = dict(os.environ, PYTHONHASHSEED=hs)
        out = subprocess.run([sys.executable, "-c", _SCRIPT], env=env,
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        digests[hs] = out.stdout.strip()
    assert len(set(digests.values())) == 1, (
        f"placement depends on PYTHONHASHSEED: {digests}")
