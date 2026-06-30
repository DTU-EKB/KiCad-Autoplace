"""Headless tests for the preflight evaluator. No pcbnew required."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import preflight                         # noqa: E402


GOOD = {
    "has_outline": True,
    "footprints": 12, "movable": 9, "locked": 3,
    "gnd_net": "/GND",
    "pours": [{"layer": "B.Cu", "net": "/GND"},
              {"layer": "B.Cu", "net": "/+24V"},
              {"layer": "B.Cu", "net": "/+24V"}],
}


def _row(rows, key):
    return next(r for r in rows if r["key"] == key)


def test_all_good_rows_ok():
    rows = preflight.evaluate(GOOD)
    assert {r["key"] for r in rows} == {"outline", "footprints", "ground", "pours"}
    assert all(r["status"] == "ok" for r in rows)
    assert "9 movable" in _row(rows, "footprints")["detail"]
    assert "3 locked" in _row(rows, "footprints")["detail"]


def test_pours_detail_lists_distinct_nets():
    detail = _row(preflight.evaluate(GOOD), "pours")["detail"]
    assert "/GND" in detail and "/+24V" in detail
    assert detail.count("/+24V") == 1                    # deduped


def test_missing_outline_warns_only_outline():
    rows = preflight.evaluate({**GOOD, "has_outline": False})
    assert _row(rows, "outline")["status"] == "warn"
    assert _row(rows, "ground")["status"] == "ok"


def test_zero_footprints_warns():
    rows = preflight.evaluate({**GOOD, "footprints": 0, "movable": 0, "locked": 0})
    assert _row(rows, "footprints")["status"] == "warn"


def test_no_gnd_net_warns():
    rows = preflight.evaluate({**GOOD, "gnd_net": None})
    assert _row(rows, "ground")["status"] == "warn"


def test_no_pours_warns():
    rows = preflight.evaluate({**GOOD, "pours": []})
    r = _row(rows, "pours")
    assert r["status"] == "warn"
    assert "route" in r["detail"].lower()                # explains the consequence
