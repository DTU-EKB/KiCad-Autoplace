"""Measure FreeRouting's run-to-run spread on ONE fixed placement.

Every correlation between a placement metric and a routed outcome is capped by
how repeatable that outcome is. If routing the *same* board twice already moves
the bridge count by two, then two predictors whose rank correlations differ by
0.03 cannot be told apart, and chasing that difference is chasing noise.

So: take one placement, route it N times from scratch, and report the spread in
the numbers the gate scores against.

  & "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" tools/router_noise.py \
        PLACED.kicad_pcb WORKDIR [runs]
"""
import os
import shutil
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import fabrication, routing, unrouted  # noqa: E402

DEFAULT_JAR = os.path.expandvars(r"%USERPROFILE%\.freerouting\freerouting-1.9.0.jar")


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    src, workdir = argv[1], argv[2]
    runs = int(argv[3]) if len(argv) > 3 else 5
    jar = os.environ.get("FREEROUTING_JAR", DEFAULT_JAR)
    passes = int(os.environ.get("ROUTE_PASSES", "10"))
    fab = os.environ.get("FAB", "cnc")
    os.makedirs(workdir, exist_ok=True)

    stage1, bridges, after = [], [], []
    for i in range(runs):
        work = os.path.join(workdir, f"noise{i}.kicad_pcb")
        shutil.copyfile(src, work)
        pro = os.path.splitext(src)[0] + ".kicad_pro"
        if os.path.exists(pro):
            shutil.copyfile(pro, os.path.splitext(work)[0] + ".kicad_pro")
        fabrication.apply_to_project(os.path.splitext(work)[0] + ".kicad_pro", fab)
        r1 = routing.route_once(work, jar, passes, sides=1)
        m1 = len(unrouted.analyse(r1["routed_pcb"])["missing"])
        r2 = routing.route_stage2_bridges(r1["routed_pcb"], jar, passes)
        m2 = len(unrouted.analyse(r2["routed_pcb"])["missing"])
        stage1.append(m1)
        bridges.append(r2["bridges"])
        after.append(m2)
        print(f"run {i}: stage1_missing={m1:>3} bridges={r2['bridges']:>3} "
              f"still_missing_after_bridging={m2:>3}", flush=True)

    def rep(name, xs):
        sd = statistics.pstdev(xs) if len(xs) > 1 else 0.0
        print(f"  {name:<34} {xs}  range={max(xs) - min(xs)}  sd={sd:.2f}")

    print("\nSAME placement, routed "
          f"{runs} times -- this is the noise floor of the gate:")
    rep("stage-1 missing connections", stage1)
    rep("wire bridges", bridges)
    rep("still missing after bridging", after)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
