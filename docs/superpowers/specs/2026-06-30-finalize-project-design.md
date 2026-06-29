# Finalize project — design

**Date:** 2026-06-30
**Status:** Approved

## Problem

After the auto-placement → route → hand-finish loop, the project directory is
littered with intermediates (`<name>.autoplaced.kicad_pcb`,
`<name>.autoplaced.refined.routed.kicad_pcb`, `.dsn`, `.ses`, `.autoplace.json`,
sidecar `.kicad_pro`s …). When the user is happy with a routed board they want
one button that promotes that finished board to be the project's main
`<name>.kicad_pcb` and deletes all the intermediates — the manual dance done
once for the system board, made repeatable.

## Decisions (from brainstorming)

- **Pick the finished file** via a file picker pre-filled to the app's last
  routed/refined output (works even when routing was finished by hand in KiCad
  and the filename chain varies).
- **Backup**: copy the current main board to `<name>.kicad_pcb.bak` (one slot,
  overwritten each finalize) before overwriting.
- **Confirm** with a native dialog listing the full plan before anything
  destructive happens.

## Behaviour

Given finished board `F` and the project's main board
`P = <dir>/<name>.kicad_pcb` (the board originally selected in the app):

1. **Backup**: copy current `P` → `<dir>/<name>.kicad_pcb.bak` (overwrite prior).
2. **Promote**: copy `F`'s bytes over `P`. Only the `.kicad_pcb` is promoted —
   the project keeps its existing `.kicad_pro` (net-class / DRC rules).
3. **Sweep temps**: in `<dir>`, delete every sibling that is a derived artifact.

If `abspath(F) == abspath(P)` (the user picked the main board itself), skip
backup + promote and only sweep temps.

## Temp-file classification (the safe delete rule)

A file in the project dir is a deletable temp iff its name starts with
`<base>.` (where `<base>` is the project board's name without extension) **and**
either:
- a dot-segment of the name is one of `autoplaced`, `refined`, `routed`, or
- the name ends with `.autoplace.json`, `.dsn`, or `.ses`.

The four core files — `<base>.kicad_pcb`, `<base>.kicad_pro`,
`<base>.kicad_sch`, `<base>.kicad_prl` — contain none of those segments and so
never match; the `<base>.kicad_pcb.bak` made in step 1 also never matches. `F`
itself (a `.routed` file) does match and is swept, which is intended.

Segment test detail: split the filename on `.`; if any segment equals
`autoplaced`/`refined`/`routed`, it is a temp. This avoids substring false
positives and matches every chain the tool produces
(`<base>.autoplaced.kicad_pcb`, `<base>.autoplaced.refined.routed.kicad_pcb`,
`<base>.autoplaced.kicad_pro`, …).

## Architecture

### New pure module `autoplace/finalize.py` (no pcbnew)

```python
TEMP_SEGMENTS = ("autoplaced", "refined", "routed")
TEMP_SUFFIXES = (".autoplace.json", ".dsn", ".ses")

def classify_temp_files(names: list[str], base: str) -> list[str]:
    # pure: return the subset of `names` that are deletable temps for `base`.

def finalize_project(finished: str, project: str, *, backup: bool = True) -> dict:
    # fs ops. Returns {"promoted": bool, "backup": str|None, "deleted": [names]}.
    # - if abspath(finished) != abspath(project):
    #     if backup: copy project -> project + ".bak"
    #     copy finished -> project
    #     promoted = True
    #   else: promoted = False, backup = None
    # - deleted = classify_temp_files(os.listdir(dir), base); unlink each.
    #   (the just-written .bak and core files are never in this set)
```

`finalize_project` computes `dir = dirname(project)`, `base = name without
.kicad_pcb`. Deletes are best-effort: a file that fails to unlink is reported in
a `"errors"` list rather than aborting the whole sweep.

### CLI: `cli.py finalize <finished> <project> [--commit]`

- **Dry-run (default)**: build the plan without touching disk — compute backup
  path, promote target, and `classify_temp_files(listdir, base)` — and print/emit
  it as JSON (`{"type":"plan","promote":…,"backup":…,"delete":[…]}`). Deletes
  nothing.
- **`--commit`**: call `finalize_project(...)` and emit the result
  (`{"type":"result","promoted":…,"backup":…,"deleted":[…],"errors":[…]}`).

Streaming/`emit` mirrors the other subcommands (one JSON object per line).
`finalize` does not need pcbnew, but runs under the same python the app already
has.

### App

- **main.js**: one `finalize` IPC handler `finalize(win, {python, finished,
  project})`:
  1. spawn `cli.py finalize <finished> <project>` (dry-run), parse the `plan`.
  2. `dialog.showMessageBox` (type "warning") summarising: promote
     `basename(finished)` → `basename(project)`, backup note, and "Delete N
     temporary files:\n<list>"; buttons `["Finalize", "Cancel"]`,
     `defaultId: 1` (Cancel), `cancelId: 1`.
  3. if confirmed, spawn `cli.py finalize <finished> <project> --commit`, parse
     the `result`, resolve `{ok:true, result}`. Otherwise `{ok:false,
     cancelled:true}`.
- **preload.js**: expose `finalize: (opts) => ipcRenderer.invoke("finalize", opts)`.
- **renderer.js**:
  - track `state.lastFinished` — set to the refine routed output
    (`report.routed_output`) when present, else the committed `state.output`.
  - new **Finalize project…** button (`#finalize`) in `.controls` after Refine,
    enabled when `state.python && state.board && !state.running`.
  - handler: call `window.api.pickBoard(...)` style picker — reuse an exposed
    picker that accepts a default path (see below) — to choose the finished
    board (default `state.lastFinished` or the project dir), then call
    `window.api.finalize({python, finished, project: state.board})`; on success
    log the promoted/deleted summary and refresh the board view from the
    promoted `state.board`.
- **Picker with default**: `pick-board` currently takes no default. Add an
  optional `defaultPath` arg to the existing `pick-board` handler
  (`dialog.showOpenDialog({ defaultPath })`) — backward compatible (undefined =
  current behaviour). `preload.pickBoard` forwards an optional arg.

### Files

- Create: `plugin/plugins/autoplace/finalize.py`, `tests/test_finalize.py`
- Modify: `cli.py` (subcommand + dispatch), `app/main.js` (finalize IPC +
  pick-board defaultPath), `app/preload.js` (finalize, pickBoard arg),
  `app/renderer/index.html` (button), `app/renderer/renderer.js` (wiring),
  `app/renderer/styles.css` (only if a new style is needed; reuse `.btn-ghost`).

## Data flow

```
Finalize click
  → pick finished .kicad_pcb (default = last routed/refined output)
  → api.finalize({finished, project: state.board})
  → main: cli.py finalize (dry-run) → plan
  → main: native confirm dialog (promote target + backup + delete list)
  → [confirm] main: cli.py finalize --commit → result
  → renderer: log "promoted X → Y, deleted N files"; reload board view
```

## Error handling

- Finished file missing / unreadable → cli exits non-zero with a message; app
  surfaces it in the log, no dialog.
- A temp that fails to delete (locked, e.g. open in KiCad) → reported in
  `errors`, the rest still deleted; app logs which ones remain.
- User cancels the confirm dialog → nothing happens (`cancelled:true`).
- `finished == project` → no backup/promote, temps still swept (the dry-run plan
  shows `promote: null`).

## Testing

Pure-Python `tests/test_finalize.py` (system python):
1. `classify_temp_files` on a realistic listing returns exactly the
   autoplaced/refined/routed/.dsn/.ses/.autoplace.json siblings and **excludes**
   `<base>.kicad_pcb`, `.kicad_pro`, `.kicad_sch`, `.kicad_prl`, and
   `<base>.kicad_pcb.bak`.
2. A foreign file (`notes.txt`, `other.kicad_pcb` with a different base) is not
   classified.
3. `finalize_project` on a tmp dir: creates `.bak` equal to the old project
   bytes, makes the project equal to the finished bytes, deletes all temps,
   returns the right dict.
4. `finalize_project` with `finished == project`: `promoted False`, `backup
   None`, temps still deleted, project bytes unchanged.

App wiring (picker, native confirm, board reload) is verified manually.
