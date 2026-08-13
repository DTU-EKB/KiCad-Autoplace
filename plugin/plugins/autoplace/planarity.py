"""Exact planarity for netlists: can this board be single-sided at all?

The placer optimises geometry -- wirelength, straight-line crossings, overlap.
But single-sided routability is not fundamentally geometric, it is
**topological**: a set of connections fits on one copper layer exactly when its
graph can be drawn in the plane without crossings. Two consequences the engine
has never exploited:

* That property is decidable **exactly**, before anything is placed. The tool
  can say "this board can be single-sided" or "this board needs at least three
  wire bridges, no matter how it is placed" -- instead of discovering it one
  60-second FreeRouting run at a time.
* Because copper bends for free, a planar netlist can be drawn with its parts at
  essentially *any* positions (Pach-Wenger: a planar graph admits a drawing with
  vertices at arbitrary prescribed points if edges may bend). So once planarity
  is established, placement stops being "avoid crossings" and becomes "realise
  this embedding with enough room" -- a far better-posed problem.

**The graph.** A net is a hyperedge: it joins every part that touches it, and on
copper the junction is a real point. So the graph is the **component/net
incidence graph** -- a node per part, a node per net, an edge for "this part has
a pad on this net". This is exact, and it dodges the trap of picking a spanning
tree per net, whose arbitrary shape would change the planarity answer.

Nets carried by a copper pour are excluded: a filled ground plane connects its
pads for free. This matters enormously -- ground is usually the highest-degree
net on the board, and leaving it in would report almost every board as
non-planar. It is also precisely the trick a person uses by hand.

**The algorithm** is Demoucron's fragment addition, O(n^2), which is plenty at
board sizes (10-130 parts) and -- unlike the linear-time tests -- produces an
explicit face list that can be checked against Euler's formula. That check is in
the test suite: a planarity verdict nobody can audit is not worth having.

Pure Python, no dependencies. Deterministic: every choice breaks ties on sorted
order, so the same graph always gives the same embedding.
"""
from __future__ import annotations

from .model import Board


# --------------------------------------------------------------------------
# plumbing
# --------------------------------------------------------------------------

def _normalise(nodes, edges):
    """Simple undirected graph: drop self-loops and parallel edges.

    Neither can affect planarity -- a second net between the same two parts is
    drawn alongside the first, and a net that begins and ends on one part needs
    no crossing -- so removing them is exact, not an approximation, and it keeps
    the fragment search from spinning on duplicates.
    """
    seen = set()
    out = []
    for a, b in edges:
        if a == b:
            continue
        key = (a, b) if _rank(a) <= _rank(b) else (b, a)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    verts = set(nodes)
    for a, b in out:
        verts.add(a)
        verts.add(b)
    return verts, out


def _rank(x):
    """Total order across mixed label types, so sorting is always defined."""
    return (type(x).__name__, x)


def _adjacency(verts, edges):
    adj = {v: set() for v in verts}
    for a, b in edges:
        adj[a].add(b)
        adj[b].add(a)
    return adj


def _components(verts, adj):
    seen = set()
    out = []
    for s in sorted(verts, key=_rank):
        if s in seen:
            continue
        stack, comp = [s], []
        seen.add(s)
        while stack:
            v = stack.pop()
            comp.append(v)
            for w in sorted(adj[v], key=_rank):
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        out.append(comp)
    return out


def _biconnected_edge_sets(verts, adj):
    """Blocks of the graph, as edge lists.

    A graph is planar exactly when every biconnected component is, so the test
    runs per block. Skipping this is a real bug source: two planar halves joined
    at a single cut vertex are planar, but a whole-graph fragment search can
    convince itself otherwise.
    """
    num = {}
    low = {}
    parent = {}
    blocks = []
    stack = []
    counter = [0]

    for root in sorted(verts, key=_rank):
        if root in num:
            continue
        work = [(root, iter(sorted(adj[root], key=_rank)))]
        num[root] = low[root] = counter[0]
        counter[0] += 1
        parent[root] = None
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in num:
                    stack.append((v, w))
                    parent[w] = v
                    num[w] = low[w] = counter[0]
                    counter[0] += 1
                    work.append((w, iter(sorted(adj[w], key=_rank))))
                    advanced = True
                    break
                if w != parent[v] and num[w] < num[v]:
                    stack.append((v, w))
                    low[v] = min(low[v], num[w])
            if advanced:
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
                if low[v] >= num[u]:
                    block = []
                    while stack:
                        e = stack.pop()
                        block.append(e)
                        if e == (u, v):
                            break
                    if block:
                        # Canonical orientation: the DFS records edges in
                        # traversal direction, and every downstream lookup keys
                        # on sorted pairs. Leaving them as-is silently breaks
                        # "is this edge already embedded?".
                        blocks.append([(a, b) if _rank(a) <= _rank(b) else (b, a)
                                       for a, b in block])
    return blocks


# --------------------------------------------------------------------------
# Demoucron: embed one biconnected block face by face
# --------------------------------------------------------------------------

def _find_cycle(verts, adj):
    """Any cycle, as a vertex list. None if the block is acyclic.

    Depth-first, carrying the live root-to-node path explicitly. An earlier
    version rebuilt the path from a ``parent`` map, which is wrong under an
    iterative DFS -- a vertex can be reached before it is expanded, so the map
    does not describe the current branch, and the walk back terminated early and
    reported "no cycle" on graphs made of nothing but cycles.
    """
    start = min(verts, key=_rank)
    stack = [(start, iter(sorted(adj[start], key=_rank)), None)]
    depth = {start: 0}
    path = [start]
    while stack:
        v, it, parent = stack[-1]
        nxt = next(it, None)
        if nxt is None:
            stack.pop()
            path.pop()
            del depth[v]
            continue
        if nxt == parent:
            continue                       # the edge we arrived on
        if nxt in depth:                   # back edge: close the cycle
            cyc = path[depth[nxt]:]
            if len(cyc) >= 3:
                return list(cyc)
            continue
        depth[nxt] = len(path)
        path.append(nxt)
        stack.append((nxt, iter(sorted(adj[nxt], key=_rank)), v))
    return None


def _fragments(block_adj, embedded_v, embedded_e):
    """Pieces of the block not yet drawn, each with its contact points.

    Two kinds, exactly as Demoucron requires: a single un-embedded edge whose
    ends are both already drawn, and a connected clump of un-drawn vertices
    together with the edges tying it to the drawing.
    """
    frags = []
    used = set()
    for a in sorted(block_adj, key=_rank):
        for b in sorted(block_adj[a], key=_rank):
            key = (a, b) if _rank(a) <= _rank(b) else (b, a)
            if key in embedded_e or key in used:
                continue
            if a in embedded_v and b in embedded_v:
                used.add(key)
                frags.append(({a, b}, [key], {a, b}))

    seen = set()
    for s in sorted(block_adj, key=_rank):
        if s in embedded_v or s in seen:
            continue
        comp, stack = set(), [s]
        seen.add(s)
        while stack:
            v = stack.pop()
            comp.add(v)
            for w in sorted(block_adj[v], key=_rank):
                if w in embedded_v or w in seen:
                    continue
                seen.add(w)
                stack.append(w)
        contacts = set()
        edges = []
        for v in comp:
            for w in block_adj[v]:
                key = (v, w) if _rank(v) <= _rank(w) else (w, v)
                if key in embedded_e:
                    continue
                edges.append(key)
                if w in embedded_v:
                    contacts.add(w)
        frags.append((comp | contacts, sorted(set(edges), key=lambda e: (_rank(e[0]), _rank(e[1]))), contacts))
    return frags


def _path_through(frag_edges, contacts, start):
    """A path across a fragment from ``start`` to another contact point."""
    adj = {}
    for a, b in frag_edges:
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    parent = {start: None}
    stack = [start]
    while stack:
        v = stack.pop()
        if v != start and v in contacts:
            path = [v]
            while parent[path[-1]] is not None:
                path.append(parent[path[-1]])
            path.reverse()
            return path
        for w in sorted(adj.get(v, ()), key=_rank):
            if w not in parent:
                parent[w] = v
                stack.append(w)
    return None


def _embed_block(block_edges):
    """Faces of a planar embedding of one biconnected block, or None."""
    verts = set()
    for a, b in block_edges:
        verts.add(a)
        verts.add(b)
    adj = _adjacency(verts, block_edges)
    if len(verts) < 3 or len(block_edges) < 3:
        return [list(verts)]                      # a bridge/edge: trivially planar

    cycle = _find_cycle(verts, adj)
    if cycle is None:
        return [list(verts)]                      # acyclic block
    faces = [list(cycle), list(reversed(cycle))]
    emb_v = set(cycle)
    emb_e = set()
    for i in range(len(cycle)):
        a, b = cycle[i], cycle[(i + 1) % len(cycle)]
        emb_e.add((a, b) if _rank(a) <= _rank(b) else (b, a))

    while True:
        frags = _fragments(adj, emb_v, emb_e)
        if not frags:
            return faces
        chosen = None
        for fv, fe, contacts in frags:
            adm = [i for i, f in enumerate(faces) if contacts <= set(f)]
            if not adm:
                return None                       # a piece fits nowhere: non-planar
            if len(adm) == 1:
                chosen = (fv, fe, contacts, adm[0])
                break
            if chosen is None:
                chosen = (fv, fe, contacts, adm[0])
        fv, fe, contacts, fi = chosen
        start = min(contacts, key=_rank)
        path = _path_through(fe, contacts, start)
        if path is None:
            return None
        face = faces.pop(fi)
        i, j = face.index(path[0]), face.index(path[-1])
        # Splitting a face with a path between two of its vertices gives two
        # faces: each way round the old face, closed by the new path.
        if i <= j:
            side_a = face[i:j + 1]
            side_b = face[j:] + face[:i + 1]
        else:
            side_a = face[i:] + face[:j + 1]
            side_b = face[j:i + 1]
        mid = path[1:-1]
        faces.append(side_a + list(reversed(mid)))
        faces.append(side_b + mid)
        for k in range(len(path) - 1):
            a, b = path[k], path[k + 1]
            emb_e.add((a, b) if _rank(a) <= _rank(b) else (b, a))
            emb_v.add(a)
            emb_v.add(b)


# --------------------------------------------------------------------------
# public: planarity
# --------------------------------------------------------------------------

def is_planar(nodes, edges) -> bool:
    """True if this graph can be drawn in the plane with no crossings."""
    verts, e = _normalise(nodes, edges)
    if not e:
        return True
    adj = _adjacency(verts, e)
    for block in _biconnected_edge_sets(verts, adj):
        bverts = set()
        for a, b in block:
            bverts.add(a)
            bverts.add(b)
        # Euler bound: a cheap certain rejection before the O(n^2) search.
        if len(bverts) >= 3 and len(block) > 3 * len(bverts) - 6:
            return False
        if _embed_block(block) is None:
            return False
    return True


def planar_faces(nodes, edges):
    """Faces of an embedding of a CONNECTED graph, or None if non-planar.

    Provided so a verdict can be audited: on a connected planar graph the result
    must satisfy V - E + F = 2. The test suite checks exactly that, because a
    planarity answer nobody can check is not worth having.
    """
    verts, e = _normalise(nodes, edges)
    verts = {v for v in verts if any(v in ed for ed in e)}
    if not e:
        return None
    adj = _adjacency(verts, e)
    if len(_components(verts, adj)) != 1:
        return None
    blocks = _biconnected_edge_sets(verts, adj)
    if len(blocks) != 1:
        return None                       # faces are only well defined per block
    return _embed_block(blocks[0])


# --------------------------------------------------------------------------
# public: skewness -- the wire bridges no placement can avoid
# --------------------------------------------------------------------------

def _minimal_nonplanar(verts, edges):
    """Shrink a non-planar edge set until every edge in it is essential.

    The result is a Kuratowski subdivision (a K5 or K3,3 in disguise), and it is
    what makes the exact search affordable: any fix must delete at least one of
    ITS edges, which is a handful rather than all few hundred on the board.
    """
    cur = list(edges)
    for e in list(cur):
        trial = [x for x in cur if x != e]
        if not is_planar(verts, trial):
            cur = trial
    return cur


def skew_edges(nodes, edges, max_bridges: int = 12):
    """A smallest set of connections whose removal leaves a planar board.

    These are the wire bridges the netlist forces. Minimum skewness is NP-hard
    in general, but the search is branch and bound over a Kuratowski core, and
    board graphs are small, so it is exact in practice; ``max_bridges`` caps the
    depth so a pathological netlist degrades to a good answer instead of hanging
    the placer.
    """
    verts, e = _normalise(nodes, edges)
    if is_planar(verts, e):
        return []

    best = [None]

    def search(cur, dropped):
        if best[0] is not None and len(dropped) >= len(best[0]):
            return
        if is_planar(verts, cur):
            best[0] = list(dropped)
            return
        if len(dropped) >= max_bridges:
            return
        core = _minimal_nonplanar(verts, cur)
        for edge in core:
            search([x for x in cur if x != edge], dropped + [edge])

    search(e, [])
    return best[0] if best[0] is not None else _minimal_nonplanar(verts, e)


def skewness(nodes, edges, max_bridges: int = 12) -> int:
    """How many wire bridges this netlist forces, whatever the placement.

    0 means single-sided is achievable and any bridge the tool ends up using is
    a placement failure, not a fact about the circuit.
    """
    return len(skew_edges(nodes, edges, max_bridges))


# --------------------------------------------------------------------------
# public: the board's own graph
# --------------------------------------------------------------------------

def netlist_graph(board: Board, planes: set[str] | None = None):
    """The component/net incidence graph whose planarity decides single-sided.

    Nodes are ``("c", ref)`` for parts and ``("n", net)`` for nets; an edge says
    "this part has a pad on this net". Nets carried by a pour are dropped, and
    so are nets with only one part on them (nothing to route).
    """
    from . import globalroute
    skip = globalroute.plane_nets(board) if planes is None else set(planes)
    members = board.nets()
    nodes, edges = set(), []
    for net in sorted(members):
        if net in skip:
            continue
        refs = sorted({ref for ref, _ in members[net]})
        if len(refs) < 2:
            continue
        nodes.add(("n", net))
        for ref in refs:
            nodes.add(("c", ref))
            edges.append((("c", ref), ("n", net)))
    return sorted(nodes), edges


def forced_bridges(board: Board, planes: set[str] | None = None,
                   max_bridges: int = 12) -> dict:
    """Can this board be single-sided, and if not, how many bridges are forced?

    The answer depends only on the netlist and which nets are poured -- not on
    where anything sits -- so it is a floor the placer cannot beat and a target
    it should be measured against.
    """
    nodes, edges = netlist_graph(board, planes)
    n_nets = sum(1 for n in nodes if n[0] == "n")
    n_comps = sum(1 for n in nodes if n[0] == "c")
    planar = is_planar(nodes, edges)
    drop = [] if planar else skew_edges(nodes, edges, max_bridges)
    return {
        "planar": planar,
        "bridges": len(drop),
        "components": n_comps,
        "nets": n_nets,
        "incidences": len(edges),
        "cut": [(a[1], b[1]) for a, b in drop],
    }
