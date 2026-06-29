"use strict";
// Main process: window, KiCad-Python detection, and the spawn bridge to cli.py.
// The renderer never touches Node/child_process directly -- everything goes
// through the typed IPC surface defined in preload.js.

const { app, BrowserWindow, ipcMain, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const fs = require("fs");
const os = require("os");

// cli.py lives at the repo root; this app sits in <repo>/app.
const REPO_ROOT = path.resolve(__dirname, "..");
const CLI_PY = path.join(REPO_ROOT, "cli.py");

// The currently-running place/refine child, so Cancel can stop it (and the
// FreeRouting Java it spawned). Only one run happens at a time.
let activeProc = null;

function sidecarPath(board) {
  return board.replace(/\.kicad_pcb$/i, "") + ".autoplace.json";
}

// Kill a child AND its descendants. cli.py spawns Java (FreeRouting) as a
// grandchild, so killing only the python leaves Java running -- on Windows
// taskkill /T walks the whole tree.
function killTree(proc) {
  if (!proc || proc.killed) return;
  if (process.platform === "win32") {
    try {
      spawn("taskkill", ["/pid", String(proc.pid), "/T", "/F"]);
    } catch {
      proc.kill("SIGKILL");
    }
  } else {
    try {
      process.kill(-proc.pid, "SIGKILL"); // process group
    } catch {
      proc.kill("SIGKILL");
    }
  }
}

// Is the FreeRouting toolchain present? Refine needs Java + the jar.
const FREEROUTING_JAR = path.join(
  os.homedir(), ".freerouting", "freerouting-1.9.0.jar"
);
function checkRefineTools() {
  return new Promise((resolve) => {
    const jar = process.env.FREEROUTING_JAR || FREEROUTING_JAR;
    const jarOk = fs.existsSync(jar);
    let proc;
    try {
      proc = spawn("java", ["-version"]);
    } catch {
      return resolve({ ok: false, java: false, jar: jarOk, jarPath: jar });
    }
    proc.on("error", () =>
      resolve({ ok: false, java: false, jar: jarOk, jarPath: jar })
    );
    proc.on("close", (code) => {
      const java = code === 0;
      resolve({ ok: java && jarOk, java, jar: jarOk, jarPath: jar });
    });
  });
}

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
      proc = spawn(python, args, {
        cwd: REPO_ROOT, env, detached: process.platform !== "win32",
      });
    } catch (e) {
      return resolve({ ok: false, error: String(e) });
    }
    activeProc = proc;

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
    proc.on("error", (e) => {
      if (activeProc === proc) activeProc = null;
      resolve({ ok: false, error: `failed to start python: ${e.message}` });
    });
    proc.on("close", (code) => {
      if (activeProc === proc) activeProc = null;
      if (proc._cancelled) return resolve({ ok: false, cancelled: true });
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

function runPlaceMulti(win, { board, python, strategy, count }) {
  return new Promise((resolve) => {
    if (!fs.existsSync(CLI_PY)) {
      return resolve({ ok: false, error: `cli.py not found at ${CLI_PY}` });
    }
    const n = count || 6;
    const send = (evt) => {
      if (!win.isDestroyed()) win.webContents.send("place-event", evt);
    };
    const env = { ...process.env, AUTOPLACE_STREAM: "1", STRATEGY: strategy || "auto" };
    const args = [CLI_PY, "place-multi", board, String(n)];
    send({ type: "log", line: `$ ${python} cli.py place-multi "${board}" ${n}` });

    let proc;
    try {
      proc = spawn(python, args, {
        cwd: REPO_ROOT, env, detached: process.platform !== "win32",
      });
    } catch (e) {
      return resolve({ ok: false, error: String(e) });
    }
    activeProc = proc;
    let stdoutBuf = "";
    let got = 0;

    const handleLine = (line) => {
      const t = line.trim();
      if (!t) return;
      if (t.startsWith("{")) {
        try {
          const obj = JSON.parse(t);
          if (obj.type === "progress")
            return send({ type: "progress", stage: obj.stage, percent: obj.percent });
          if (obj.type === "candidate" || obj.type === "candidate-error") {
            if (obj.type === "candidate") got++;
            return send(obj);
          }
          if (obj.type === "done") return send(obj);
        } catch {
          /* fall through to log */
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
    proc.stderr.on("data", (chunk) =>
      chunk.toString().split("\n").forEach((l) => l.trim() && send({ type: "log", line: l }))
    );
    proc.on("error", (e) => {
      if (activeProc === proc) activeProc = null;
      resolve({ ok: false, error: `failed to start python: ${e.message}` });
    });
    proc.on("close", (code) => {
      if (activeProc === proc) activeProc = null;
      if (proc._cancelled) return resolve({ ok: false, cancelled: true });
      if (stdoutBuf.trim()) handleLine(stdoutBuf);
      if (got > 0) resolve({ ok: true, count: got });
      else resolve({ ok: false, error: `place-multi exited ${code} without candidates (check the log)` });
    });
  });
}

function runRefine(win, { board, python, seed, budget, passes }) {
  return new Promise((resolve) => {
    if (!fs.existsSync(CLI_PY)) {
      return resolve({ ok: false, error: `cli.py not found at ${CLI_PY}` });
    }
    const stem = board.replace(/\.kicad_pcb$/i, "");
    const out = stem + ".refined.kicad_pcb";
    const send = (evt) => {
      if (!win.isDestroyed()) win.webContents.send("place-event", evt);
    };
    const env = { ...process.env, AUTOPLACE_STREAM: "1" };
    if (budget) env.REFINE_BUDGET = String(budget);   // effort -> loop length
    if (passes) env.REFINE_PASSES = String(passes);   // FreeRouting passes/route
    const args = [CLI_PY, "refine", board, out, String(seed ?? 0)];
    send({ type: "log", line: `$ ${python} cli.py refine "${board}" (budget ${budget || "default"}, ${passes || "default"} passes)` });

    let proc;
    try {
      proc = spawn(python, args, {
        cwd: REPO_ROOT, env, detached: process.platform !== "win32",
      });
    } catch (e) {
      return resolve({ ok: false, error: String(e) });
    }
    activeProc = proc;
    let stdoutBuf = "";
    let result = null;
    const handleLine = (line) => {
      const t = line.trim();
      if (!t) return;
      if (t.startsWith("{")) {
        try {
          const obj = JSON.parse(t);
          if (obj.type === "iteration") return send({ type: "iteration", ...obj });
          if (obj.type === "progress")
            return send({ type: "progress", stage: obj.stage, percent: obj.percent });
          if (obj.type === "result") {
            result = obj;
            return send({ type: "result", report: obj });
          }
        } catch {
          /* fall through to log */
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
    proc.stderr.on("data", (chunk) =>
      chunk.toString().split("\n").forEach((l) => l.trim() && send({ type: "log", line: l }))
    );
    proc.on("error", (e) => {
      if (activeProc === proc) activeProc = null;
      resolve({ ok: false, error: `failed to start python: ${e.message}` });
    });
    proc.on("close", (code) => {
      if (activeProc === proc) activeProc = null;
      if (proc._cancelled) return resolve({ ok: false, cancelled: true });
      if (stdoutBuf.trim()) handleLine(stdoutBuf);
      if (result) resolve({ ok: true, report: result, output: out });
      else resolve({ ok: false, error: `refine exited ${code} without a result (check the log)` });
    });
  });
}

function dumpBoard(python, board) {
  return new Promise((resolve) => {
    if (!fs.existsSync(CLI_PY)) {
      return resolve({ ok: false, error: `cli.py not found at ${CLI_PY}` });
    }
    let proc;
    try {
      proc = spawn(python, [CLI_PY, "dump", board], { cwd: REPO_ROOT });
    } catch (e) {
      return resolve({ ok: false, error: String(e) });
    }
    let out = "";
    let err = "";
    proc.stdout.on("data", (d) => (out += d.toString()));
    proc.stderr.on("data", (d) => (err += d.toString()));
    proc.on("error", (e) => resolve({ ok: false, error: e.message }));
    proc.on("close", (code) => {
      if (code !== 0) {
        return resolve({ ok: false, error: err.trim() || `dump exited ${code}` });
      }
      try {
        resolve({ ok: true, geometry: JSON.parse(out) });
      } catch (e) {
        resolve({ ok: false, error: "bad dump JSON: " + e.message });
      }
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
  ipcMain.handle("run-place-multi", (_e, opts) => runPlaceMulti(win, opts));

  ipcMain.handle("run-refine", (_e, opts) => runRefine(win, opts));

  ipcMain.handle("cancel-run", () => {
    if (activeProc) {
      activeProc._cancelled = true;
      killTree(activeProc);
      return true;
    }
    return false;
  });

  ipcMain.handle("check-refine-tools", () => checkRefineTools());

  ipcMain.handle("dump-board", (_e, { python, board }) =>
    dumpBoard(python, board)
  );

  ipcMain.handle("load-connectors", (_e, { board }) => {
    const p = sidecarPath(board);
    try {
      if (fs.existsSync(p)) {
        return JSON.parse(fs.readFileSync(p, "utf8")).connectors || null;
      }
    } catch {
      /* fall through */
    }
    return null;
  });

  ipcMain.handle("save-connectors", (_e, { board, connectors }) => {
    try {
      fs.writeFileSync(sidecarPath(board), JSON.stringify({ connectors }, null, 2));
      return true;
    } catch {
      return false;
    }
  });

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
