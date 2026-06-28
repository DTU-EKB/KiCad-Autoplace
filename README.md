# KiCad-Autoplace

Connectivity-aware **automatic PCB component placement** for KiCad 9, maintained
for students at DTU Ballerup Campus. A companion to
[KiCad-components](https://github.com/DTU-EKB/KiCad-components): that repo gives
you the symbols and footprints, this one places them.

Lock the parts you care about (connectors, mounting holes), press the button, and
the engine places the rest — minimising wirelength and keeping the board
single-sided-routable so the existing routing workflow can finish it.

> Status: **0.1.0 / development.** Pipeline: block detection → force-directed
> global → simulated-annealing refine (translate / rotate / swap) → legalize.
> Runs on **KiCad 9 and 10** boards, always overlap-free, and beats the
> hand-placement on wirelength *and* crossings for most boards (mppt −56% /
> crossings 17→6, current_sense −56% / 10→3, feedback_circuit −69% / 22→1,
> system −36% / 438→272). See [`docs/BUILD_SPEC.md`](docs/BUILD_SPEC.md) §7 for the
> full table and the known weak case (`motor_power`, a dense spacious board).

## Install (KiCad Plugin & Content Manager)
Go to *Plugin and Content Manager* on the KiCad project screen → *Manage…* beside
the Repository dropdown → **+** → paste:
```
https://raw.githubusercontent.com/DTU-EKB/KiCad-Autoplace/main/repository.json
```
Refresh the manager, install **DTU EKB Autoplace**, restart KiCad. A toolbar
button appears in the PCB editor.

## Use
1. Open your board in the PCB editor.
2. **Lock** the footprints that must stay put (edge connectors, mounting holes).
3. Click **Autoplace (DTU-EKB)** in the toolbar.
4. Review the before/after report (HPWL, crossings, overlaps). Reload if needed.

The engine never moves locked footprints and guarantees an overlap-free result.

## Develop / run headless
The engine core is pure Python (no `pcbnew`); only `kicad_io` touches KiCad. Run
the dev CLI with KiCad's bundled Python:
```powershell
& "C:\Program Files\KiCad\9.0\bin\python.exe" cli.py metrics  board.kicad_pcb
& "C:\Program Files\KiCad\9.0\bin\python.exe" cli.py place    board.kicad_pcb out.kicad_pcb
```
`place` writes `<stem>.autoplaced.kicad_pcb` by default — it never overwrites the
input unless you pass an output path equal to it.

Unit tests for the headless core run under any Python:
```bash
python -m pytest tests/
```

## Layout
```
plugin/                 # zipped & installed by PCM
  metadata.json         # PCM package manifest (type: plugin)
  plugins/              # lands in KiCad's 3rd-party plugins dir
    __init__.py         # registers the Action Plugin
    action_autoplace.py # toolbar button -> runs the engine on the open board
    autoplace/          # the engine (pure-Python core + kicad_io bridge)
  resources/icon.png
repository.json         # PCM repo feed (add this URL to KiCad)
packages.json           # PCM package list
docs/BUILD_SPEC.md      # full design spec & milestones
cli.py                  # dev / headless runner
tests/                  # headless unit tests
```

## License
MIT — see [LICENSE](LICENSE).
