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
        board = pcbnew.GetBoard()
        path = board.GetFileName()
        model, pcb = kicad_io.load_board(path)

        if not model.free():
            self._msg("Nothing to place", "All footprints are locked.")
            return

        report = engine.place(model)
        kicad_io.apply_placement(model, pcb, path)
        pcbnew.Refresh()

        b, a = report["before"], report["after"]
        self._msg(
            "Autoplace complete",
            f"Components: {a['components']}  (free: {len(model.free())})\n"
            f"HPWL:      {b['hpwl_mm']:.0f} -> {a['hpwl_mm']:.0f} mm "
            f"({report['hpwl_delta_pct']:+.0f}%)\n"
            f"Crossings: {b['crossings']} -> {a['crossings']}\n"
            f"Overlaps:  {report['overlaps_remaining']}\n\n"
            "Reload the board if positions don't refresh.",
        )

    @staticmethod
    def _msg(title, text):
        try:
            import wx
            wx.MessageBox(text, title, wx.OK | wx.ICON_INFORMATION)
        except Exception:
            print(f"[{title}] {text}")
