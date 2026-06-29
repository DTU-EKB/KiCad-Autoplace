"use strict";
// Renderer: wires the dashboard to the main-process bridge (window.api).

const $ = (id) => document.getElementById(id);

const state = {
  python: null, // verified KiCad python path
  board: null, // selected .kicad_pcb
  running: false,
};

// ---- python status ---------------------------------------------------------
function setPython(info) {
  state.python = info && info.pythonPath;
  const pill = $("pythonPill");
  const text = $("pythonText");
  const change = $("changePython");
  change.hidden = false;
  pill.classList.remove("pill-ok", "pill-bad", "pill-wait");
  if (state.python) {
    pill.classList.add("pill-ok");
    text.textContent = info.kicadVersion
      ? `KiCad ${info.kicadVersion}`
      : "KiCad Python ready";
    text.title = state.python;
  } else {
    pill.classList.add("pill-bad");
    text.textContent = "KiCad Python not found";
    text.title = "";
  }
  refreshRunEnabled();
}

async function detect() {
  setPillWait("Detecting KiCad…");
  const info = await window.api.detectPython();
  setPython(info);
}

function setPillWait(msg) {
  const pill = $("pythonPill");
  pill.classList.remove("pill-ok", "pill-bad");
  pill.classList.add("pill-wait");
  $("pythonText").textContent = msg;
}

// ---- board picker ----------------------------------------------------------
async function pickBoard() {
  const p = await window.api.pickBoard();
  if (!p) return;
  state.board = p;
  $("boardPath").textContent = p;
  $("boardPath").classList.remove("muted");
  refreshRunEnabled();
}

function refreshRunEnabled() {
  $("run").disabled = !(state.python && state.board && !state.running);
}

// ---- run -------------------------------------------------------------------
function setProgress(stage, percent) {
  $("progressWrap").hidden = false;
  $("progressStage").textContent = stageLabel(stage);
  const pct = Math.max(0, Math.min(100, percent || 0));
  $("progressPct").textContent = `${pct.toFixed(0)}%`;
  $("bar").style.width = `${pct}%`;
}

function stageLabel(stage) {
  return (
    {
      load: "Loading board…",
      analyze: "Analyzing connectivity…",
      seed: "Seeding layout…",
      anneal: "Optimizing placement…",
      legalize: "Removing overlaps…",
      done: "Finishing…",
    }[stage] || "Working…"
  );
}

function appendLog(line) {
  const log = $("log");
  log.textContent += line + "\n";
  log.scrollTop = log.scrollHeight;
}

function fmt(n) {
  return typeof n === "number" ? n.toLocaleString() : n;
}

function setDelta(el, pct, lowerIsBetter = true) {
  if (pct === null || pct === undefined) {
    el.textContent = "";
    return;
  }
  const improved = lowerIsBetter ? pct < 0 : pct > 0;
  el.textContent = `${pct > 0 ? "+" : ""}${pct}%`;
  el.classList.remove("delta-good", "delta-bad");
  el.classList.add(improved ? "delta-good" : "delta-bad");
}

function showResults(report, output) {
  const b = report.before;
  const a = report.after;
  $("results").hidden = false;

  $("mHpwl").textContent = fmt(Math.round(a.hpwl_mm));
  setDelta($("mHpwlDelta"), report.hpwl_delta_pct, true);

  $("mCross").textContent = fmt(a.crossings);
  const cd = report.crossings_delta;
  const cdEl = $("mCrossDelta");
  cdEl.textContent = `${cd > 0 ? "+" : ""}${cd} vs ${b.crossings}`;
  cdEl.classList.remove("delta-good", "delta-bad");
  cdEl.classList.add(cd <= 0 ? "delta-good" : "delta-bad");

  $("mOverlap").textContent = fmt(report.overlaps_remaining);
  $("mComps").textContent = fmt(a.components);
  $("mBlocks").textContent = `${report.blocks} block${
    report.blocks === 1 ? "" : "s"
  }`;

  $("outPath").textContent = output;
  $("projNote").textContent = report.project_copied
    ? "· net-class rules carried over"
    : "";
  state.output = output;

  const badge = $("resultBadge");
  badge.textContent = `${report.overlaps_remaining === 0 ? "overlap-free" : "needs review"} · seed ${report.seed}`;
}

async function run() {
  if (state.running) return;
  state.running = true;
  refreshRunEnabled();
  $("results").hidden = true;
  $("log").textContent = "";
  setProgress("load", 0);

  const opts = {
    board: state.board,
    python: state.python,
    strategy: $("strategy").value,
    seed: parseInt($("seed").value, 10) || 0,
  };

  const res = await window.api.runPlace(opts);

  state.running = false;
  refreshRunEnabled();

  if (res.ok) {
    setProgress("done", 100);
    showResults(res.report, res.output);
  } else {
    setProgress("done", 100);
    $("progressStage").textContent = "Failed";
    appendLog("ERROR: " + res.error);
    openLog(true);
  }
}

// ---- log toggle ------------------------------------------------------------
function openLog(force) {
  const log = $("log");
  const chev = document.querySelector(".chev");
  const open = force === undefined ? log.hidden : force;
  log.hidden = !open;
  chev.classList.toggle("open", open);
}

// ---- wire up ---------------------------------------------------------------
window.api.onPlaceEvent((evt) => {
  if (evt.type === "progress") setProgress(evt.stage, evt.percent);
  else if (evt.type === "result") showResults(evt.report, evt.report.output);
  else if (evt.type === "log") appendLog(evt.line);
});

$("pickBoard").addEventListener("click", pickBoard);
$("run").addEventListener("click", run);
$("changePython").addEventListener("click", async () => {
  setPillWait("Selecting…");
  const info = await window.api.pickPython();
  if (info.pythonPath) setPython(info);
  else detect();
});
$("reveal").addEventListener("click", () => {
  if (state.output) window.api.revealPath(state.output);
});
$("logToggle").addEventListener("click", () => openLog());

async function init() {
  await detect();
  const dev = await window.api.devConfig();
  if (dev && dev.board) {
    state.board = dev.board;
    $("boardPath").textContent = dev.board;
    $("boardPath").classList.remove("muted");
    refreshRunEnabled();
    if (dev.autorun && state.python) run();
  }
}

init();
