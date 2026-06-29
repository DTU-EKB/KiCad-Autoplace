"""KiCad Action Plugin: connectivity-aware auto-placement of the open board.

Adds a toolbar button to the PCB editor. Running it places all *unlocked*
footprints using the force-directed + legalize engine, then shows a short
before/after report. Lock the parts you want to pin (connectors, mounting holes)
before running -- the engine treats locked footprints as fixed obstacles.
"""
import os

import pcbnew

from autoplace import engine, kicad_io


class AutoplaceAction(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Autoplace (DTU-EKB)"
        self.category = "Placement"
        self.description = (
            "Connectivity-aware automatic component placement. "
            "Locked footprints stay fixed."
        )
        self.show_toolbar_button = True
        icon = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon):
            self.icon_file_name = icon

    def Run(self):
        # Operate on the board ALREADY OPEN in the editor -- build the model from
        # it and write positions straight back to the same footprint objects, so
        # the change shows up live. (Loading a separate copy from disk and saving
        # over the open file does not update the editor and corrupts the session.)
        pcb = pcbnew.GetBoard()
        model = kicad_io.build_model(pcb)

        if not model.free():
            self._msg("Nothing to place",
                      "All footprints are locked. Unlock the parts you want the "
                      "tool to move (keep connectors / mounting holes locked).")
            return

        try:
            report = engine.place(model)
        except Exception as exc:                      # never leave a half-placed board
            self._msg("Autoplace failed", f"{type(exc).__name__}: {exc}")
            return

        kicad_io.apply_to_board(model, pcb)
        try:
            pcbnew.Refresh()
        except Exception:
            pass

        b, a = report["before"], report["after"]
        self._msg(
            "Autoplace complete",
            f"Placed {len(model.free())} free of {a['components']} components "
            f"in {report['blocks']} blocks.\n"
            f"HPWL:      {b['hpwl_mm']:.0f} -> {a['hpwl_mm']:.0f} mm "
            f"({report['hpwl_delta_pct']:+.0f}%)\n"
            f"Crossings: {b['crossings']} -> {a['crossings']}\n"
            f"Overlaps:  {report['overlaps_remaining']}\n\n"
            "Review, then save (Ctrl+S) if you want to keep it, or undo (Ctrl+Z).",
        )

    @staticmethod
    def _msg(title, text):
        try:
            import wx
            wx.MessageBox(text, title, wx.OK | wx.ICON_INFORMATION)
        except Exception:
            print(f"[{title}] {text}")
