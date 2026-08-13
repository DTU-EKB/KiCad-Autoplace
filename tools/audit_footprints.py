"""Two pre-run checks the planarity census cannot make, over real boards.

**Part A -- footprint clearance audit.** A footprint whose own pad-to-pad gap is
under the process clearance fails DRC wherever it is placed. A 2.54 mm pin
header with 2.0 mm lands leaves 0.54 mm where the CNC profile wants 0.85, and
that alone produced 28 DRC clearance errors on a board whose routing was
otherwise perfect. Nothing downstream can fix it -- only a different footprint
can -- so it is worth knowing before a placement run rather than after a routed
one. Reported per footprint *class*, because the fix is to the library.

**Part B -- point model vs pad-accurate model.** ``planarity.forced_bridges``
contracts every component to a point, so it believes copper walks straight
through a part. ``escape`` puts the pads back and marks the gaps no track fits
through as walls. Neither model dominates the other and the spread between them
is the result, so all four readings are printed side by side: the point model,
the pad model with only genuinely blocked gaps walled, the pad model with every
part treated as a solid obstacle, and the pad model including the gaps this
particular placement has closed between neighbouring parts.

Real land sizes come from ``pcbnew`` -- ``model.Pad`` carries a position and a
net but no land size -- so this needs KiCad's interpreter:

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/audit_footprints.py \\
        --json out.json BOARD.kicad_pcb [BOARD2 ...]
"""
import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

import pcbnew                                          # noqa: E402

from autoplace import escape, fabrication, kicad_io    # noqa: E402

# KiCad pad shapes whose size is the two axes of an ellipse. Everything else
# (rectangle, rounded rectangle, chamfered rectangle, trapezoid) is measured as
# a sharp rectangle, which understates a rounded pad's gap by its corner radius
# -- conservative, and the alternative is threading a corner radius through
# every comparison to move a diagonal gap by a tenth of a millimetre.
_ELLIPTICAL = (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_OVAL)

# A land small enough to clash with nothing, used for pads that carry no copper
# (a plated-through hole is copper; an NPTH mounting hole is a drill and KiCad
# checks those under a separate hole-to-hole rule). Zero would divide by zero in
# ``land_reach``; a picometre is far below any real board dimension.
_NO_COPPER = escape.Land(1e-9, 1e-9)

# How far a pad's orientation may sit from an axis before its half-extents are
# taken from the rotated bounding box instead of being swapped. THT footprints
# are drawn at multiples of 90 degrees, so this only fires on the unusual ones,
# and the bounding box over-states the land -- it can flag a pair that is
# actually clear, never clear one that is not.
_AXIS_TOL_DEG = 1e-6


def _size(pad):
    """Pad size in mm, across the KiCad 9/10 padstack signature change."""
    try:
        v = pad.GetSize(pcbnew.F_Cu)
    except TypeError:
        v = pad.GetSize()
    return pcbnew.ToMM(v.x), pcbnew.ToMM(v.y)


def _shape(pad):
    try:
        return pad.GetShape(pcbnew.F_Cu)
    except TypeError:
        return pad.GetShape()


def _land(pad) -> escape.Land:
    """One pad's real land, with its half-extents in board axes.

    ``escape.Land`` is defined in board axes so it needs no orientation
    bookkeeping of its own; the swap for a pad turned onto its side happens
    here, where ``pcbnew`` reports the absolute angle.
    """
    if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH or not pad.IsOnCopperLayer():
        return _NO_COPPER
    w, h = _size(pad)
    hx, hy = w / 2.0, h / 2.0
    deg = pad.GetOrientation().AsDegrees() % 180.0
    if abs(deg - 90.0) <= _AXIS_TOL_DEG:
        hx, hy = hy, hx
    elif deg > _AXIS_TOL_DEG:
        rad = math.radians(deg)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        hx, hy = hx * c + hy * s, hx * s + hy * c
    return escape.Land(hx, hy, _shape(pad) in _ELLIPTICAL)


def board_lands(pcb) -> dict:
    """``(ref, pad index) -> Land`` for every pad, in ``kicad_io``'s pad order.

    Keyed on the index ``kicad_io.build_model`` assigns, which is the order
    ``fp.Pads()`` yields -- the same iteration, on the same live board, so the
    two agree by construction rather than by convention.
    """
    out = {}
    for fp in pcb.GetFootprints():
        ref = fp.GetReference()
        for i, pad in enumerate(fp.Pads()):
            out[(ref, i)] = _land(pad)
    return out


def _fpids(pcb) -> dict:
    return {fp.GetReference(): fp.GetFPIDAsString() for fp in pcb.GetFootprints()}


# --------------------------------------------------------------------------
# Part A report
# --------------------------------------------------------------------------

def audit_rows(clashes: dict, fpids: dict) -> list[dict]:
    """Group the offending refs by footprint class, worst gap first.

    The fix for a sub-clearance footprint is made in the library, once, and then
    every ref using it is repaired -- so the class is the unit of the report and
    the refs are the evidence. Sorted by how far under the clearance the worst
    pair is, because that is the order to fix them in.
    """
    by_class: dict[str, dict] = {}
    for ref, found in clashes.items():
        key = fpids.get(ref, "?")
        row = by_class.setdefault(key, {"fpid": key, "refs": [], "errors": 0,
                                        "worst": None, "required": 0.0,
                                        "example": None})
        row["refs"].append(ref)
        row["errors"] += len(found)
        for c in found:
            if row["worst"] is None or c.gap < row["worst"]:
                row["worst"] = c.gap
                row["required"] = c.required
                row["example"] = f"{ref} pad {c.pad_a}-{c.pad_b}"
    for row in by_class.values():
        row["refs"].sort()
        row["worst"] = round(row["worst"], 4)
    return sorted(by_class.values(), key=lambda r: (r["worst"], r["fpid"]))


# --------------------------------------------------------------------------
# one board
# --------------------------------------------------------------------------

def run(path: str, args) -> dict:
    model, pcb = kicad_io.load_board(path)
    lands = board_lands(pcb)
    clearance = fabrication.margin_for(args.fab)
    track = fabrication.track_for(args.fab)

    t0 = time.perf_counter()
    clashes = escape.board_clashes(model, clearance=clearance, lands=lands)
    audit = audit_rows(clashes, _fpids(pcb))
    audit_ms = (time.perf_counter() - t0) * 1000.0

    t0 = time.perf_counter()
    if args.audit_only:
        models = {}
    else:
        # The barrier analysis keeps padblock's nominal 2.0 mm land rather than
        # the real ones read above: ``component_blocked_pairs`` takes one land
        # diameter for the whole footprint, and the nominal is what the
        # placement cost already scores, so this stays the same geometry.
        # It errs the safe way here. Most real THT lands on these boards are
        # 1.6-1.7 mm, so the nominal eats ~0.4 mm more copper than the board
        # does and can wall a gap that is really open (only in the narrow band
        # of 4.7-5.1 mm centre distance). That biases the model towards
        # *more* barriers and so towards more forced bridges -- which is the
        # direction that makes a reported zero worth believing.
        models = escape.compare(model, track=track, clearance=clearance,
                                max_bridges=args.max_bridges,
                                max_probes=args.max_probes)
    compare_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "board": path,
        "tag": os.path.basename(path).replace(".kicad_pcb", ""),
        "fab": args.fab,
        "clearance": clearance,
        "track": track,
        "parts": len(model.components),
        "planes": sorted(model.planes),
        "bad_footprints": len(clashes),
        "drc_errors": sum(len(v) for v in clashes.values()),
        "audit": audit,
        "models": models,
        "audit_ms": round(audit_ms, 1),
        "compare_ms": round(compare_ms, 1),
    }


def _cell(m: dict | None) -> str:
    if not m:
        return "-"
    n = m.get("bridges")
    if n is None:
        return "n/a"
    return f"{n}{'+' if m.get('capped') else ''}"


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("boards", nargs="+")
    ap.add_argument("--json", dest="out", default=None,
                    help="write the full result here")
    ap.add_argument("--fab", default="cnc", choices=sorted(fabrication.PROFILES),
                    help="fabrication profile supplying track and clearance")
    ap.add_argument("--max-bridges", type=int, default=6,
                    help="depth cap for the exact bridge search")
    ap.add_argument("--max-probes", type=int, default=20000,
                    help="planarity probes the search may spend (deterministic)")
    ap.add_argument("--audit-only", action="store_true",
                    help="Part A only: skip the model comparison")
    ap.add_argument("--quiet-audit", action="store_true",
                    help="omit the per-footprint detail under the table")
    args = ap.parse_args(argv[1:])

    rows = []
    print(f"{'board':<18} {'parts':>5} {'point':>6} {'pad/blocked':>12} "
          f"{'pad/solid':>10} {'pad/placed':>11} {'bad fp':>7} {'drc':>5}")
    for path in args.boards:
        try:
            row = run(path, args)
        except Exception as exc:                       # a bad board must not
            print(f"{os.path.basename(path):<18} failed: {exc}")   # stop the run
            continue
        rows.append(row)
        m = row["models"]
        print(f"{row['tag']:<18} {row['parts']:>5} "
              f"{_cell(m.get('point')):>6} {_cell(m.get('blocked')):>12} "
              f"{_cell(m.get('solid')):>10} {_cell(m.get('placed')):>11} "
              f"{row['bad_footprints']:>7} {row['drc_errors']:>5}", flush=True)

    if not args.quiet_audit:
        print("\nfootprints below the fabrication clearance "
              f"({rows[0]['clearance'] if rows else '?'} mm):")
        seen = {}
        for row in rows:
            for a in row["audit"]:
                e = seen.setdefault(a["fpid"], {"worst": a["worst"], "req":
                                                a["required"], "boards": [],
                                                "errors": 0, "example":
                                                a["example"]})
                e["worst"] = min(e["worst"], a["worst"])
                e["errors"] += a["errors"]
                e["boards"].append(f"{row['tag']}:{','.join(a['refs'])}")
        if not seen:
            print("  none -- every footprint clears its own pads")
        for fpid, e in sorted(seen.items(), key=lambda kv: kv[1]["worst"]):
            print(f"  {e['worst']:>7.3f} mm  (needs {e['req']:.2f})  "
                  f"{e['errors']:>4} DRC  {fpid}")
            print(f"{'':>26}{e['example']} | " + "; ".join(e["boards"]))

    print("\n'+' marks a bridge count that works but was not proven minimal.")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
