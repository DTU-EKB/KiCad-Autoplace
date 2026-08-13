"""Diagnose what stops a board reaching 100% routed.

Routes a board once at the requested side count and reports not just the
percentage but the *identity* of every connection FreeRouting could not make --
net name and the pads it failed to join. That list is what any escalation
strategy (re-place, grow the board, insert a jumper) has to act on, and today
nothing in the engine can see it: routing.route_once returns counts only.

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools\\diag_unrouted.py <board.kicad_pcb> [sides]
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugin", "plugins"))

import pcbnew  # noqa: E402

from autoplace import routing  # noqa: E402

JAR = os.path.expanduser("~/.freerouting/freerouting-1.9.0.jar")


def unrouted_pairs(pcb):
    """[(net, refA.padA, refB.padB, mm)] for every remaining ratsnest edge."""
    pcb.BuildConnectivity()
    conn = pcb.GetConnectivity()
    out = []
    for netcode in range(1, pcb.GetNetCount()):
        ni = pcb.FindNet(netcode)
        if ni is None:
            continue
        try:
            edges = conn.GetRatsnestForNet(netcode)
        except Exception:
            continue
        if not edges:
            continue
        for e in edges:
            try:
                a, b = e.GetSourceNode(), e.GetTargetNode()
                pa, pb = a.Parent(), b.Parent()
                ra = f"{pa.GetParentFootprint().GetReference()}.{pa.GetNumber()}"
                rb = f"{pb.GetParentFootprint().GetReference()}.{pb.GetNumber()}"
                d = ((a.Pos().x - b.Pos().x) ** 2 + (a.Pos().y - b.Pos().y) ** 2) ** 0.5
                out.append((ni.GetNetname(), ra, rb, round(pcbnew.ToMM(int(d)), 2)))
            except Exception as exc:                     # pragma: no cover
                out.append((ni.GetNetname(), "?", "?", f"({exc})"))
    return out


def main():
    board_path = sys.argv[1]
    sides = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    r = routing.route_once(board_path, JAR, passes=20, sides=sides)
    pcb = pcbnew.LoadBoard(r["routed_pcb"])
    pairs = unrouted_pairs(pcb)

    tracks = list(pcb.GetTracks())
    f_cu = sum(1 for t in tracks if t.GetLayer() == pcbnew.F_Cu)
    b_cu = sum(1 for t in tracks if t.GetLayer() == pcbnew.B_Cu)
    vias = sum(1 for t in tracks if t.GetClass() == "PCB_VIA")

    print(json.dumps({
        "sides": sides,
        "pct": round(r["pct"], 1),
        "total_connections": r["total"],
        "routed": r["routed"],
        "unrouted": r["unrouted"],
        "seconds": r["seconds"],
        "tracks_F_Cu": f_cu, "tracks_B_Cu": b_cu, "vias": vias,
        "unrouted_pairs": pairs,
    }, indent=1))


if __name__ == "__main__":
    main()
