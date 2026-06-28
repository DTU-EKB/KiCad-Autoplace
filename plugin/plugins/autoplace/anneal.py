"""Simulated-annealing detailed placement (M4).

Refines the force-directed seed with a full multi-objective cost and moves that
escape local minima (nudge + swap). Cost is evaluated *incrementally* -- only the
nets and pairs touching the moved component(s) are recomputed -- so thousands of
moves run in a fraction of a second even on the 131-part system board.

Cost = HPWL(signal) + overlap-area(hard) + connector-edge + block-cohesion.

Orientation/rotation moves are intentionally deferred (they touch the model,
metrics and kicad_io); this pass is translation + swap only and keeps the layout
overlap-free for the final legalize step.
"""
from __future__ import annotations

import math

from .blocks import block_centroids
from .metrics import _is_power
from .model import Board


class _Weights:
    HPWL = 1.0
    OVERLAP = 60.0        # per mm^2 of courtyard overlap -- effectively a barrier
    EDGE = 0.6            # connector distance to nearest edge
    COHESION = 0.35       # component distance to its block centroid


class Annealer:
    def __init__(self, board: Board, *, margin: float = 0.8, seed: int = 0):
        import random
        self.board = board
        self.margin = margin
        self.rng = random.Random(seed)
        self.comps = list(board.components.values())
        self.free = [c for c in self.comps if not c.locked]
        # net -> list of (comp, pad) for signal nets only
        self.comp_nets: dict[str, set[str]] = {c.ref: set() for c in self.comps}
        self.net_members: dict[str, list[tuple]] = {}
        for net, members in board.nets().items():
            if _is_power(net):
                continue
            self.net_members[net] = [(board.components[r], pi) for r, pi in members]
            for r, _ in members:
                self.comp_nets[r].add(net)
        self.centroids = block_centroids(board)

    # ---- cost pieces -----------------------------------------------------
    def _net_hpwl(self, net: str) -> float:
        pts = [c.pad_world(c.pads[pi]) for c, pi in self.net_members[net]]
        if len(pts) < 2:
            return 0.0
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    @staticmethod
    def _overlap_area(a, b, margin) -> float:
        ox = (a.w + b.w) / 2 + margin - abs(a.x - b.x)
        oy = (a.h + b.h) / 2 + margin - abs(a.y - b.y)
        if ox > 0 and oy > 0:
            return ox * oy
        return 0.0

    def _edge_dist(self, c) -> float:
        b = self.board
        return min(c.x - b.x0, b.x1 - c.x, c.y - b.y0, b.y1 - c.y)

    def _cohesion(self, c) -> float:
        cx, cy = self.centroids.get(c.block, (c.x, c.y))
        return math.hypot(c.x - cx, c.y - cy)

    def local_cost(self, subset) -> float:
        """Cost attributable to a set of components (for move deltas)."""
        W = _Weights
        cost = 0.0
        nets = set()
        for c in subset:
            nets |= self.comp_nets[c.ref]
        for net in nets:
            cost += W.HPWL * self._net_hpwl(net)
        seen = set()
        for c in subset:
            for other in self.comps:
                if other is c:
                    continue
                key = (c.ref, other.ref) if c.ref < other.ref else (other.ref, c.ref)
                if key in seen:
                    continue
                seen.add(key)
                cost += W.OVERLAP * self._overlap_area(c, other, self.margin)
        for c in subset:
            if c.is_connector:
                cost += W.EDGE * self._edge_dist(c)
            cost += W.COHESION * self._cohesion(c)
        return cost

    def _clamp(self, c):
        b, m = self.board, self.margin
        hw, hh = c.w / 2, c.h / 2
        c.x = min(max(c.x, b.x0 + hw + m), b.x1 - hw - m)
        c.y = min(max(c.y, b.y0 + hh + m), b.y1 - hh - m)

    # ---- main loop -------------------------------------------------------
    def run(self, *, steps: int = 6000, t0: float = 8.0, t_end: float = 0.05):
        if len(self.free) < 2:
            return
        cooling = (t_end / t0) ** (1.0 / steps)
        T = t0
        best = self._snapshot()
        best_cost = self._total_cost()
        running = best_cost
        resync_every = max(200, steps // 20)

        for it in range(steps):
            if self.rng.random() < 0.65:
                delta = self._try_nudge(T)
            else:
                delta = self._try_swap()
            if delta is not None:
                running += delta
                if running < best_cost - 1e-9:
                    best_cost = running
                    best = self._snapshot()
            T *= cooling
            if (it + 1) % resync_every == 0:
                self.centroids = block_centroids(self.board)
                running = self._total_cost()      # cohesion target moved

        self._restore(best)

    def _try_nudge(self, T):
        c = self.rng.choice(self.free)
        ox, oy = c.x, c.y
        before = self.local_cost((c,))
        amp = max(1.0, T)
        c.x += (self.rng.random() - 0.5) * 2 * amp
        c.y += (self.rng.random() - 0.5) * 2 * amp
        self._clamp(c)
        after = self.local_cost((c,))
        return self._accept(after - before, T, lambda: self._revert1(c, ox, oy))

    def _try_swap(self):
        if len(self.free) < 2:
            return None
        a, b = self.rng.sample(self.free, 2)
        before = self.local_cost((a, b))
        a.x, b.x = b.x, a.x
        a.y, b.y = b.y, a.y
        self._clamp(a)
        self._clamp(b)
        after = self.local_cost((a, b))
        # swaps are accepted greedily (T applied to nudges, the finer moves)
        return self._accept(after - before, 0.0,
                            lambda: self._revert2(a, b))

    def _accept(self, delta, T, revert):
        if delta <= 0 or (T > 1e-9 and self.rng.random() < math.exp(-delta / T)):
            return delta
        revert()
        return None

    @staticmethod
    def _revert1(c, ox, oy):
        c.x, c.y = ox, oy

    @staticmethod
    def _revert2(a, b):
        a.x, b.x = b.x, a.x
        a.y, b.y = b.y, a.y

    def _total_cost(self) -> float:
        W = _Weights
        cost = sum(W.HPWL * self._net_hpwl(n) for n in self.net_members)
        for i in range(len(self.comps)):
            for j in range(i + 1, len(self.comps)):
                cost += W.OVERLAP * self._overlap_area(
                    self.comps[i], self.comps[j], self.margin)
        for c in self.comps:
            if c.is_connector:
                cost += W.EDGE * self._edge_dist(c)
            cost += W.COHESION * self._cohesion(c)
        return cost

    def _snapshot(self):
        return {c.ref: (c.x, c.y) for c in self.free}

    def _restore(self, snap):
        for ref, (x, y) in snap.items():
            c = self.board.components[ref]
            c.x, c.y = x, y


def anneal(board: Board, *, seed: int = 0, steps: int = 6000, margin: float = 0.8):
    Annealer(board, margin=margin, seed=seed).run(steps=steps)
    return board
