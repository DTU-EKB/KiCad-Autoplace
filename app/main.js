"use strict";
// Main process: window, KiCad-Python detection, and the spawn bridge to cli.py.
// The renderer never touches Node/child_process directly -- everything goes
// through the typed IPC surface defined in preload.js.

const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");

// cli.py lives at the repo root; this app sits in <repo>/app.
const REPO_ROOT = path.resolve(__dirname, "..");
const CLI_PY = path.join(REPO_ROOT, "cli.py");

// --- KiCad Python discovery -------------------------------------------------
// kicad_io.py imports pcbnew, which only exists in KiCad's bundled Python.
// System python will NOT work. We scan the standard install locations and let
// the user override with a manual pick if auto-detection misses.

function candidatePythons() {
  const out = [];
  const seen = new Set();
  const add = (p) => {
    if (p && !seen.has(p) && fs.existsSync(p)) {
      seen.add(p);
      out.push(p);
    }
  };

  // explicit override wins
  add(process.env.AUTOPLACE_PYTHON);
  add(process.env.KICAD_PYTHON);

  if (process.platform === "win32") {
    const roots = [
      "C:\\Program Files\\KiCad",
      "C:\\Program Files (x86)\\KiCad",
    ];
    for (const root of roots) {
      let vers = [];
      try {
        vers = fs.readdirSync(root); // e.g. "10.0", "9.0"
      } catch {
        continue;
      }
      // newest version first
      vers
        .sort((a, b) => parseFloat(b) - parseFloat(a))
        .forEach((v) => add(path.join(root, v, "bin", "python.exe")));
    }
  } else if (process.platform === "darwin") {
    add(
      "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
    );
  } else {
    // Linux: pcbnew typically registers into the system python3.
    for (const p of ["/usr/bin/python3", "/usr/local/bin/python3"]) add(p);
  }
  return out;
}

// Confirm a python can actually `import pcbnew` (the real requirement).
function verifyPython(py) {
  return new Promise((resolve) => {
    let proc;
    try {
      proc = spawn(py, ["-c", "import pcbnew; print(pcbnew.GetBuildVersion())"]);
    } catch {
      return resolve(null);
    }
    let ver = "";
    proc.stdout.on("data", (d) => (ver += d.toString()));
    proc.on("error", () => resolve(null));
    proc.on("close", (code) => resolve(code === 0 ? ver.trim() : null));
  });
}

async function detectPython() {
  const cands = candidatePythons();
  for (const py of cands) {
    const ver = await verifyPython(py);
    if (ver) return { pythonPath: py, kicadVersion: ver, candidates: cands };
  }
  return { pythonPath: null, kicadVersion: null, candidates: cands };
}

// --- the placement run ------------------------------------------------------
// Spawns `python cli.py place IN OUT SEED` in streaming mode and forwards each
// NDJSON line to the renderer as a progress/result/log event.

function runPlace(win, { board, python, strategy, seed }) {
  return new Promise((resolve) => {
    if (!fs.existsSync(CLI_PY)) {
      return resolve({ ok: false, error: `cli.py not found at ${CLI_PY}` });
    }
    const stem = board.replace(/\.kicad_pcb$/i, "");
    const out = stem + ".autoplaced.kicad_pcb";

    const send = (evt) => {
      if (!win.isDestroyed()) win.webContents.send("place-event", evt);
    };

    const env = {
      ...process.env,
      AUTOPLACE_STREAM: "1",
      STRATEGY: strategy || "auto",
    };
    const args = [CLI_PY, "place", board, out, String(seed ?? 0)];
    send({ type: "log", line: `$ ${python} cli.py place "${board}" ...` });

    let proc;
    try {
      proc = spawn(python, args, { cwd: REPO_ROOT, env });
    } catch (e) {
      return resolve({ ok: false, error: String(e) });
    }

    let stdoutBuf = "";
    let result = null;

    const handleLine = (line) => {
      const t = line.trim();
      if (!t) return;
      if (t.startsWith("{")) {
        try {
          const obj = JSON.parse(t);
          if (obj.type === "progress") {
            send({ type: "progress", stage: obj.stage, percent: obj.percent });
            return;
          }
          if (obj.type === "result") {
            result = obj;
            send({ type: "result", report: obj });
            return;
          }
        } catch {
          /* not a JSON line -- fall through to log */
        }
      }
      send({ type: "log", line });
    };

    proc.stdout.on("data", (chunk) => {
      stdoutBuf += chunk.toString();
      let nl;
      while ((nl = stdoutBuf.indexOf("\n")) >= 0) {
        handleLine(stdoutBuf.slice(0, nl));
        stdoutBuf = stdoutBuf.slice(nl + 1);
      }
    });
    proc.stderr.on("data", (chunk) => {
      chunk
        .toString()
        .split("\n")
        .forEach((l) => l.trim() && send({ type: "log", line: l }));
    });
    proc.on("error", (e) =>
      resolve({ ok: false, error: `failed to start python: ${e.message}` })
    );
    proc.on("close", (code) => {
      if (stdoutBuf.trim()) handleLine(stdoutBuf);
      if (result) resolve({ ok: true, report: result, output: out });
      else
        resolve({
          ok: false,
          error: `cli.py exited ${code} without a result (check the log)`,
        });
    });
  });
}

// --- IPC --------------------------------------------------------------------
function registerIpc(win) {
  ipcMain.handle("detect-python", () => detectPython());

  ipcMain.handle("pick-python", async () => {
    const filters =
      process.platform === "win32"
        ? [{ name: "python.exe", extensions: ["exe"] }]
        : [{ name: "python", extensions: ["*"] }];
    const r = await dialog.showOpenDialog(win, {
      title: "Select KiCad's python executable",
      properties: ["openFile"],
      filters,
    });
    if (r.canceled || !r.filePaths[0]) return { pythonPath: null };
    const py = r.filePaths[0];
    const ver = await verifyPython(py);
    return { pythonPath: py, kicadVersion: ver };
  });

  ipcMain.handle("pick-board", async () => {
    const r = await dialog.showOpenDialog(win, {
      title: "Select a KiCad board",
      properties: ["openFile"],
      filters: [{ name: "KiCad PCB", extensions: ["kicad_pcb"] }],
    });
    return r.canceled ? null : r.filePaths[0] || null;
  });

  ipcMain.handle("run-place", (_e, opts) => runPlace(win, opts));

  // Dev/demo hook: preload a board (and optionally auto-run) from env, so the
  // full GUI flow can be exercised without manual file-dialog navigation.
  ipcMain.handle("dev-config", () => ({
    board: process.env.AUTOPLACE_DEV_BOARD || null,
    autorun: process.env.AUTOPLACE_DEV_AUTORUN === "1",
  }));

  ipcMain.handle("reveal-path", (_e, p) => {
    if (p && fs.existsSync(p)) shell.showItemInFolder(p);
    return true;
  });
}

// --- window -----------------------------------------------------------------
function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 780,
    minWidth: 880,
    minHeight: 640,
    backgroundColor: "#0f1117",
    title: "DTU-EKB AutoPlace",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.setMenuBarVisibility(false);
  win.loadFile(path.join(__dirname, "renderer", "index.html"));
  if (process.argv.includes("--dev")) win.webContents.openDevTools();
  registerIpc(win);
}

app.whenReady().then(() => {
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
