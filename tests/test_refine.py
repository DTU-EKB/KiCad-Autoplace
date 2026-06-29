"""Pure tests for the keep-best/patience refinement loop. No pcbnew/FreeRouting."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import refine                                # noqa: E402


def _make(pcts):
    """route_eval returning scripted routed-% values in call order."""
    seq = iter(pcts)
    calls = {"step": 0}
    def route_eval(model):
        return next(seq), None                 # (pct, field)
    def step(model, field):
        calls["step"] += 1
        return f"cand{calls['step']}"          # a distinct candidate marker
    return route_eval, step, calls


def test_keeps_best_only_on_improvement_beyond_margin():
    # initial 90; candidates route 90.2 (within margin, reject), 95 (accept)
    route_eval, step, _ = _make([90.0, 90.2, 95.0, 94.0, 94.5, 94.9])
    r = refine.keep_best_loop("init", route_eval, step,
                              budget=5, patience=2, margin=1.0)
    assert r["best_pct"] == 95.0
    assert r["best"] == "cand2"


def test_patience_stops_after_non_improving_iters():
    route_eval, step, calls = _make([90.0, 90.1, 90.1, 90.1, 90.1])
    r = refine.keep_best_loop("init", route_eval, step,
                              budget=10, patience=2, margin=1.0)
    assert r["best_pct"] == 90.0
    assert r["best"] == "init"                 # never improved
    assert calls["step"] == 2                  # stopped after 2 non-improving


def test_stops_at_100_without_stepping():
    route_eval, step, calls = _make([100.0])
    r = refine.keep_best_loop("init", route_eval, step,
                              budget=10, patience=3, margin=1.0)
    assert r["best_pct"] == 100.0
    assert calls["step"] == 0                   # already done, no refinement


def test_warm_starts_from_best_not_last_candidate():
    # step1 from "init"(90) -> c1 routes 95 (accept); step2 from "c1"(95) -> c2
    # routes 80 (reject); step3 must warm-start from best "c1" again, not the
    # rejected "c2".
    seen = []
    seq = iter([90.0, 95.0, 80.0, 99.0])

    def route_eval(model):
        return next(seq), None

    def step(model, field):
        seen.append(model)
        return f"c{len(seen)}"

    refine.keep_best_loop("init", route_eval, step,
                          budget=3, patience=5, margin=1.0)
    assert seen[0] == "init"
    assert seen[1] == "c1"
    assert seen[2] == "c1"
