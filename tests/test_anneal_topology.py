"""Keeping an untangled layout untangled through the anneal.

``anneal._quality`` selects which visited layout to keep, on wirelength plus the
overlap barrier. Neither term knows anything about crossings, so a placement
seeded crossing-free from a planar embedding gets traded away for a few
millimetres of wire. The whole point of seeding topologically is lost unless the
selection metric can see topology too.

The weight defaults to 0.0, so every existing placement result is bit-identical
until a caller asks for it. That is deliberate: the repo's rule is that a
behaviour change ships only once it has beaten the baseline on routed boards.

  python -m pytest tests/test_anneal_topology.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import anneal, globalroute                  # noqa: E402
from autoplace.model import Board, Component, Pad          # noqa: E402


def _part(ref, x, y, *nets, w=3.0, h=3.0):
    return Component(ref=ref, w=w, h=h, x=x, y=y,
                     pads=[Pad(str(i + 1), n, 0.0, 0.0) for i, n in enumerate(nets)])


def _board(*parts, w=100.0, h=100.0):
    b = Board(0.0, 0.0, w, h)
    b.components = {p.ref: p for p in parts}
    return b


def _crossings(b):
    return len(globalroute.conflicts(globalroute.net_segments(b)))


def _tangled():
    """Two 2-pin nets wired so their straight-line trees cross."""
    return _board(_part("A1", 10, 10, "N1"), _part("A2", 60, 60, "N1"),
                  _part("B1", 10, 60, "N2"), _part("B2", 60, 10, "N2"))


# --------------------------------------------------------------------------
# the knob exists and is off by default
# --------------------------------------------------------------------------

def test_default_behaviour_is_unchanged():
    """Zero weight must reproduce the old selection metric exactly, or every
    previously measured placement result silently moves."""
    a, b = _tangled(), _tangled()
    anneal.anneal(a, seed=3, steps=400)
    anneal.anneal(b, seed=3, steps=400, cross_weight=0.0)
    assert [(c.ref, c.x, c.y, c.rot) for c in a.components.values()] == \
           [(c.ref, c.x, c.y, c.rot) for c in b.components.values()]


def test_quality_charges_for_crossings_when_asked():
    b = _tangled()
    plain = anneal.Annealer(b, seed=0)
    topo = anneal.Annealer(b, seed=0, cross_weight=50.0)
    assert topo._quality() > plain._quality()


def test_quality_is_unchanged_on_a_layout_with_no_crossings():
    b = _board(_part("A1", 10, 10, "N1"), _part("A2", 20, 10, "N1"),
               _part("B1", 10, 60, "N2"), _part("B2", 20, 60, "N2"))
    assert _crossings(b) == 0
    plain = anneal.Annealer(b, seed=0)
    topo = anneal.Annealer(b, seed=0, cross_weight=50.0)
    assert topo._quality() == plain._quality()


def test_weight_scales_the_charge():
    b = _tangled()
    base = anneal.Annealer(b, seed=0)._quality()
    one = anneal.Annealer(b, seed=0, cross_weight=10.0)._quality()
    two = anneal.Annealer(b, seed=0, cross_weight=20.0)._quality()
    assert abs((two - base) - 2 * (one - base)) < 1e-6


# --------------------------------------------------------------------------
# it actually keeps layouts untangled
# --------------------------------------------------------------------------

def test_an_untangled_seed_survives_the_anneal():
    """A crossing-free start that the plain metric is happy to tangle. With the
    topology term the anneal must not hand back a worse-crossing layout than it
    started with."""
    b = _board(*[_part(f"R{i}", 10 + 9 * i, 50, f"N{i}", f"N{i + 1}")
                 for i in range(8)])
    start = _crossings(b)
    assert start == 0
    anneal.anneal(b, seed=7, steps=3000, cross_weight=200.0)
    assert _crossings(b) <= start


def test_it_is_still_deterministic():
    a, b = _tangled(), _tangled()
    anneal.anneal(a, seed=11, steps=800, cross_weight=25.0)
    anneal.anneal(b, seed=11, steps=800, cross_weight=25.0)
    assert [(c.ref, c.x, c.y, c.rot) for c in a.components.values()] == \
           [(c.ref, c.x, c.y, c.rot) for c in b.components.values()]


def test_engine_passes_the_weight_through():
    from autoplace import engine
    b = _board(*[_part(f"R{i}", 10 + 9 * i, 50, f"N{i}", f"N{i + 1}")
                 for i in range(8)])
    engine.place(b, seed=0, strategy="keep", sa_steps=500, cross_weight=100.0,
                 aesthetic=False)
    for c in b.components.values():
        assert b.x0 <= c.left and c.right <= b.x1
