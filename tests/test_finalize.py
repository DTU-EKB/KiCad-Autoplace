"""Headless tests for project finalize (promote + sweep temps). No pcbnew.

  python -m pytest tests/test_finalize.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "plugins"))

from autoplace import finalize                          # noqa: E402


CORE = [
    "system.kicad_pcb", "system.kicad_pro",
    "system.kicad_sch", "system.kicad_prl",
]
TEMPS = [
    "system.autoplaced.kicad_pcb", "system.autoplaced.kicad_pro",
    "system.autoplaced.refined.kicad_pcb",
    "system.autoplaced.refined.routed.kicad_pcb",
    "system.refined.kicad_pcb", "system.routed.kicad_pcb",
    "system.autoplace.json",
    "system.dsn", "system.ses",
]
FOREIGN = ["notes.txt", "power.kicad_pcb", "power.autoplaced.kicad_pcb",
           "README.md", "system.kicad_pcb.bak"]


def test_classify_selects_only_temps():
    names = CORE + TEMPS + FOREIGN
    got = set(finalize.classify_temp_files(names, "system"))
    assert got == set(TEMPS)


def test_classify_excludes_core_and_backup():
    got = set(finalize.classify_temp_files(CORE + ["system.kicad_pcb.bak"], "system"))
    assert got == set()


def _write(p, text):
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)


def _setup_dir(tmp_path):
    d = tmp_path
    _write(d / "system.kicad_pcb", "ORIGINAL")
    _write(d / "system.kicad_pro", "PROJECT-RULES")
    for t in TEMPS:
        _write(d / t, "temp-" + t)
    _write(d / "system.autoplaced.refined.routed.kicad_pcb", "FINISHED-ROUTED")
    return d


def test_finalize_promotes_and_sweeps(tmp_path):
    d = _setup_dir(tmp_path)
    finished = str(d / "system.autoplaced.refined.routed.kicad_pcb")
    project = str(d / "system.kicad_pcb")

    res = finalize.finalize_project(finished, project)

    assert res["promoted"] is True
    assert res["backup"] == str(d / "system.kicad_pcb.bak")
    # backup holds the old project bytes
    assert (d / "system.kicad_pcb.bak").read_text() == "ORIGINAL"
    # project now equals the finished board
    assert (d / "system.kicad_pcb").read_text() == "FINISHED-ROUTED"
    # all temps gone, core + backup kept
    assert not (d / "system.autoplaced.kicad_pcb").exists()
    assert not (d / "system.autoplaced.refined.routed.kicad_pcb").exists()
    assert not (d / "system.dsn").exists()
    assert (d / "system.kicad_pro").exists()
    assert (d / "system.kicad_pcb.bak").exists()
    assert set(res["deleted"]) == set(TEMPS)


def test_finalize_finished_equals_project_only_sweeps(tmp_path):
    d = _setup_dir(tmp_path)
    project = str(d / "system.kicad_pcb")

    res = finalize.finalize_project(project, project)

    assert res["promoted"] is False
    assert res["backup"] is None
    assert (d / "system.kicad_pcb").read_text() == "ORIGINAL"   # unchanged
    assert not (d / "system.dsn").exists()                       # temps still swept
    assert set(res["deleted"]) == set(TEMPS)
