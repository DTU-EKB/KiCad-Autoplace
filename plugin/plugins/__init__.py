"""KiCad entry point. pcbnew imports this at startup and we register the plugin."""
import os
import sys

# Make the bundled 'autoplace' package importable regardless of how KiCad loads
# this folder (relative-import behaviour varies across KiCad point releases).
sys.path.insert(0, os.path.dirname(__file__))

try:
    from action_autoplace import AutoplaceAction
    AutoplaceAction().register()
except Exception as exc:  # never break PCB editor startup over a plugin error
    import traceback
    sys.stderr.write("KiCad-Autoplace failed to register:\n")
    traceback.print_exc()
