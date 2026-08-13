"""Can each of these boards be single-sided at all -- and if not, by how much?

For every board: build the component/net incidence graph, drop the poured nets,
and decide planarity exactly. The answer depends only on the netlist, not on any
placement, so it splits the wire bridges a board ends up needing into two very
different things:

  * **forced** -- the netlist is not planar and no placement can avoid them;
  * **avoidable** -- the placer put them there.

Until now the tool could not tell those apart, so it treated every bridge as a
fact of life. Needs pcbnew only to read the board:

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/planarity_census.py \
        OUT.json BOARD.kicad_pcb [BOARD2 ...]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import kicad_io, planarity  # noqa: E402


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    out_path, boards = argv[1], argv[2:]
    rows = []
    print(f"{'board':<20} {'parts':>5} {'nets':>5} {'planar':>7} "
          f"{'forced_bridges':>15} {'ms':>7}")
    for path in boards:
        tag = os.path.basename(path).replace(".kicad_pcb", "")
        try:
            model, _ = kicad_io.load_board(path)
        except Exception as exc:
            print(f"{tag:<20} load failed: {exc}")
            continue
        t0 = time.perf_counter()
        r = planarity.forced_bridges(model)
        ms = (time.perf_counter() - t0) * 1000.0
        r.update(board=path, tag=tag, ms=round(ms, 1),
                 planes=sorted(model.planes))
        rows.append(r)
        print(f"{tag:<20} {r['components']:>5} {r['nets']:>5} "
              f"{('YES' if r['planar'] else 'no'):>7} {r['bridges']:>15} "
              f"{ms:>7.1f}")
        if not r["planar"]:
            for a, b in r["cut"][:6]:
                print(f"{'':<20}   cut: {a} -- {b}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    n_planar = sum(1 for r in rows if r["planar"])
    print(f"\n{n_planar}/{len(rows)} boards are single-sided-able with zero "
          f"wire bridges, on topology alone.")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
