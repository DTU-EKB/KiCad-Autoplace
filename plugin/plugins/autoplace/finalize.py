"""Finalize a project: promote a finished board and sweep the intermediates.

Pure-Python (no pcbnew). The placement/route pipeline leaves derived files next
to the project board -- ``<base>.autoplaced.kicad_pcb``,
``<base>.autoplaced.refined.routed.kicad_pcb``, ``.dsn`` / ``.ses`` /
``.autoplace.json``, sidecar ``.kicad_pro``s. When the user is happy with a
routed board, ``finalize_project`` copies it over ``<base>.kicad_pcb`` (after a
one-slot ``.bak``) and deletes those temps. The core project files
(``.kicad_pcb`` / ``.kicad_pro`` / ``.kicad_sch`` / ``.kicad_prl``) and the
backup are never swept.
"""
from __future__ import annotations

import os
import shutil

TEMP_SEGMENTS = ("autoplaced", "refined", "routed")
TEMP_SUFFIXES = (".autoplace.json", ".dsn", ".ses")


def classify_temp_files(names: list[str], base: str) -> list[str]:
    """Subset of ``names`` that are deletable temps for project ``base``.

    A name qualifies iff it starts with ``base + "."`` and either a dot-segment
    equals one of TEMP_SEGMENTS, or it ends with one of TEMP_SUFFIXES.
    """
    prefix = base + "."
    out = []
    for n in names:
        if not n.startswith(prefix):
            continue
        segments = n.split(".")
        if any(seg in TEMP_SEGMENTS for seg in segments) or \
                any(n.endswith(suf) for suf in TEMP_SUFFIXES):
            out.append(n)
    return out


def finalize_project(finished: str, project: str, *, backup: bool = True) -> dict:
    """Promote ``finished`` to ``project`` (with a .bak) and sweep temps.

    Returns ``{"promoted", "backup", "deleted", "errors"}``. When ``finished``
    and ``project`` are the same file, only the sweep runs.
    """
    directory = os.path.dirname(os.path.abspath(project))
    base = os.path.basename(project)
    if base.endswith(".kicad_pcb"):
        base = base[: -len(".kicad_pcb")]

    same = os.path.abspath(finished) == os.path.abspath(project)
    promoted = False
    backup_path = None
    if not same:
        if backup:
            backup_path = project + ".bak"
            shutil.copyfile(project, backup_path)
        shutil.copyfile(finished, project)
        promoted = True

    deleted, errors = [], []
    for name in classify_temp_files(os.listdir(directory), base):
        try:
            os.remove(os.path.join(directory, name))
            deleted.append(name)
        except OSError as exc:
            errors.append({"file": name, "error": str(exc)})

    return {"promoted": promoted, "backup": backup_path,
            "deleted": deleted, "errors": errors}
