"""Simulated-annealing detailed placement (M4).

Refines the force-directed seed with a full multi-objective cost and moves that
escape local minima (nudge + swap). Cost is evaluated *incrementally* -- only the
nets and pairs touching the moved component(s) are recomputed -- so thousands of
moves run in a fraction of a second even on the 131-part system board.

Two distinct costs, deliberately:
  * SEARCH cost (guides which moves are accepted) =
        HPWL(signal) + overlap-area(hard) + routing-channel + connector-edge
        + block-cohesion.
  * SELECTION cost (decides which visited layout we keep, ``_quality``) =
        HPWL(signal) + overlap-area(hard) only.
The soft terms (channel / cohesion / edge) shape the search but must NOT pick the
returned layout: ranking by the full cost made the engine discard the low-HPWL
layouts it found and return overlap/cohesion-optimal but wirelength-bad ones.

Moves: nudge (translate), rotate (0/90/180/270) and swap. Overlap is a hard
barrier in the cost, and the final legalize step guarantees an overlap-free board.
"""
from __future__ import annotations

import math

from . import geom
from .blocks import block_centroids
from .edge import pin_to_edge
from .metrics import _is_power
from .model import Board


class _Weights:
    HPWL = 1.0
    OVERLAP = 60.0        # per mm^2 of courtyard overlap -- effectively a barrier
    EDGE = 0.6            # connector distance to nearest edge
    COHESION = 0.35       # component distance to its block centroid
    CHANNEL = 4.0         # soft penalty for gaps narrower than a routing channel
    CONG_K = 3.0          # per-unit-pressure multiplier on the channel term


# Desired clear gap between courtyards so the router has a channel (mm).
CHANNEL_MM = 2.6          # 1.0 mm track + 2 x 0.8 mm clearance (DTU fiber-laser DR)


class Annealer:
    def __init__(self, board: Board, *, margin: float = 0.8, seed: int = 0,
                 channel_scale: float = 1.0, cohesion_scale: float = 1.0,
                 congestion=None):
        import random
        self.board = board
        self.margin = margin
        self.channel = _Weights.CHANNEL * channel_scale
        self.cohesion = _Weights.COHESION * cohesion_scale
        # per-component channel multiplier from the previous routing's congestion
        # (sampled once at the component's start position; fixed for this pass)
        self.cpress = {}
        if congestion is not None and not getattr(congestion, "empty", False):
            self.cpress = {c.ref: congestion.pressure_at(c.x, c.y)
                           for c in board.components.values()}
        self.rng = random.Random(seed)
        self.comps = list(board.components.values())
        self.free = [c for c in self.comps if not c.locked]
        # parts the rotate/swap moves may touch (edge connectors are excluded:
        # they keep their assigned orientation and only slide along their edge)
        self.movable = [c for c in self.free if not c.edge]
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

    def _pair_penalty(self, a, b, margin) -> float:
        """Hard overlap area (barrier) + soft channel penalty for tight gaps."""
        # gap between courtyards along each axis (negative => overlapping)
        gx = abs(a.x - b.x) - (a.eff_w + b.eff_w) / 2
        gy = abs(a.y - b.y) - (a.eff_h + b.eff_h) / 2
        ox = margin - gx
        oy = margin - gy
        cost = 0.0
        if ox > 0 and oy > 0:                          # boxes overlap
            cost += _Weights.OVERLAP * ox * oy
        # channel: penalise when the nearer-axis gap is below CHANNEL_MM and the
        # boxes shadow each other on the other axis (a real routing pinch point)
        gap = max(gx, gy)
        shadow = min(gx, gy) < margin
        if self.channel and shadow and 0 <= gap < CHANNEL_MM:
            press = self.cpress.get(a.ref, 0.0) + self.cpress.get(b.ref, 0.0)
            local = self.channel * (1.0 + _Weights.CONG_K * press / 2.0)
            cost += local * (CHANNEL_MM - gap)
        return cost

    def _edge_dist(self, c) -> float:
        b = self.board
        return min(c.x - b.x0, b.x1 - c.x, c.y - b.y0, b.y1 - c.y)

    def _cohesion(self, c) -> float:
        cx, cy = self.centroids.get(c.block, (c.x, c.y))
        return math.hypot(c.x - cx, c.y - cy)

    def _quality(self) -> float:
        """Selection metric: wirelength + the hard overlap barrier only.

        The soft terms (channel / cohesion / connector-edge) shape the *search* --
        they bias which moves the annealer accepts -- but they must NOT decide which
        of the visited layouts we keep. Including them (as the old full-cost ``best``
        tracking did) pulls the retained layout away from the low-wirelength ones the
        anneal actually finds: cohesion alone could trade ~2000 mm of HPWL for parts
        sitting tighter on their block centroids, so the engine routinely *returned a
        worse layout than it had already visited*. Ranking kept layouts by placement
        quality fixes that.
        """
        q = sum(self._net_hpwl(n) for n in self.net_members)
        m = self.margin
        comps = self.comps
        for i in range(len(comps)):
            a = comps[i]
            for j in range(i + 1, len(comps)):
                b = comps[j]
                ox = m - (abs(a.x - b.x) - (a.eff_w + b.eff_w) / 2)
                oy = m - (abs(a.y - b.y) - (a.eff_h + b.eff_h) / 2)
                if ox > 0 and oy > 0:
                    q += _Weights.OVERLAP * ox * oy
        return q

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
                cost += self._pair_penalty(c, other, self.margin)
        for c in subset:
            if c.is_connector:
                cost += W.EDGE * self._edge_dist(c)
            cost += self.cohesion * self._cohesion(c)
        return cost

    def _clamp(self, c):
        geom.clamp_center(c, self.board, self.margin)

    # ---- main loop -------------------------------------------------------
    def run(self, *, steps: int = 6000, t0: float = 8.0, t_end: float = 0.05,
            progress=None):
        if len(self.free) < 2:
            return
        cooling = (t_end / t0) ** (1.0 / steps)
        T = t0
        best = self._snapshot()
        best_q = self._quality()              # keep the best layout by QUALITY
        resync_every = max(200, steps // 20)
        # Sample the quality metric ~300x over the run (O(n^2), so not every step)
        # and snapshot whenever the layout improves. The seed is the initial best,
        # so the anneal can never return a layout worse than where it started.
        sample_every = max(1, steps // 300)

        for it in range(steps):
            roll = self.rng.random()
            if roll < 0.55:
                self._try_nudge(T)
            elif roll < 0.80:
                self._try_rotate(T)
            else:
                self._try_swap()
            T *= cooling
            if (it + 1) % sample_every == 0:
                q = self._quality()
                if q < best_q - 1e-9:
                    best_q = q
                    best = self._snapshot()
            if (it + 1) % resync_every == 0:
                self.centroids = block_centroids(self.board)   # cohesion target moved
                if progress is not None:
                    progress((it + 1) / steps)

        q = self._quality()                   # don't miss the final state
        if q < best_q - 1e-9:
            best = self._snapshot()
        self._restore(best)

    def _try_nudge(self, T):
        c = self.rng.choice(self.free)
        ox, oy = c.x, c.y
        before = self.local_cost((c,))
        amp = max(1.0, T)
        if c.edge:
            d = (self.rng.random() - 0.5) * 2 * amp
            if c.edge in ("L", "R"):
                c.y += d
            else:
                c.x += d
            pin_to_edge(c, self.board, self.margin)
        else:
            c.x += (self.rng.random() - 0.5) * 2 * amp
            c.y += (self.rng.random() - 0.5) * 2 * amp
        self._clamp(c)
        after = self.local_cost((c,))
        return self._accept(after - before, T, lambda: self._revert1(c, ox, oy))

    def _try_rotate(self, T):
        if not self.movable:
            return None
        c = self.rng.choice(self.movable)
        old_rot = c.rot
        before = self.local_cost((c,))
        c.rot = self.rng.choice([r for r in (0, 90, 180, 270) if r != old_rot])
        self._clamp(c)                       # eff dims changed -> re-clamp
        after = self.local_cost((c,))
        return self._accept(after - before, T,
                            lambda: self._revert_rot(c, old_rot))

    @staticmethod
    def _revert_rot(c, old_rot):
        c.rot = old_rot

    def _try_swap(self):
        if len(self.movable) < 2:
            return None
        a, b = self.rng.sample(self.movable, 2)
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

    def _snapshot(self):
        return {c.ref: (c.x, c.y, c.rot) for c in self.free}

    def _restore(self, snap):
        for ref, (x, y, rot) in snap.items():
            c = self.board.components[ref]
            c.x, c.y, c.rot = x, y, rot


def anneal(board: Board, *, seed: int = 0, steps: int = 6000, margin: float = 0.8,
           channel_scale: float = 1.0, cohesion_scale: float = 1.0,
           congestion=None, progress=None):
    Annealer(board, margin=margin, seed=seed, channel_scale=channel_scale,
             cohesion_scale=cohesion_scale, congestion=congestion).run(
                 steps=steps, progress=progress)
    return board
