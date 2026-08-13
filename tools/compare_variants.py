"""Line up every placement variant against the forced-bridge lower bound.

Reads the batch.json files written by ``tools/batch_singlesided.py`` -- one
directory per variant -- and prints, per board, the bridges each variant's best
placement actually needed after routing, next to the number the netlist forces.

The forced count is the target. A variant is only interesting if it closes the
gap to it, and the gap is the whole point: until the planarity census existed
there was no way to tell a bridge the circuit demands from one the placer left
behind, so every variant looked equally good.

  py -3.13 tools/compare_variants.py CENSUS.json NAME=DIR [NAME=DIR ...]
"""
import json
import os
import sys


def _load(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _summary(rows):
    """(best bridges among placements that fully closed, n, zero-bridge count)."""
    usable = [r for r in rows if r.get("missing_final") is not None]
    closed = [r for r in usable if r["missing_final"] == 0]
    best = min((r["bridges"] for r in closed), default=None)
    zero = sum(1 for r in closed if r["bridges"] == 0)
    return best, len(usable), zero


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    census = {r["tag"]: r for r in _load(argv[1])}
    variants = {}
    for spec in argv[2:]:
        name, _, d = spec.partition("=")
        variants[name] = _load(os.path.join(d, "batch.json"))

    boards = sorted({t for v in variants.values() for t in v})
    names = list(variants)
    head = f"{'board':<18}{'forced':>7}" + "".join(f"{n:>16}" for n in names)
    print(head)
    print("-" * len(head))
    totals = {n: [0, 0] for n in names}       # [sum of best bridges, boards counted]
    for tag in boards:
        forced = census.get(tag, {}).get("bridges")
        cells = []
        for n in names:
            data = variants[n].get(tag)
            if not data:
                cells.append(f"{'-':>16}")
                continue
            best, cnt, zero = _summary(data["rows"])
            cells.append(f"{f'{best} ({zero}/{cnt} at 0)':>16}"
                         if best is not None else f"{'no close':>16}")
            if best is not None:
                totals[n][0] += best
                totals[n][1] += 1
        print(f"{tag:<18}{str(forced):>7}" + "".join(cells))

    print("-" * len(head))
    print(f"{'TOTAL bridges':<18}{'':>7}" +
          "".join(f"{f'{totals[n][0]} over {totals[n][1]}':>16}" for n in names))
    print("\n'best (k/n at 0)' = fewest bridges any placement needed, and how "
          "many of the\nplacements that fully closed did so with none at all. "
          "Lower is better; 'forced'\nis the floor no placement can beat.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
