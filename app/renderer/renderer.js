"use strict";
// Renderer: wires the dashboard to the main-process bridge (window.api).

const $ = (id) => document.getElementById(id);

const BLOCK_COLORS = [
  "#2a78d6", "#1baf7a", "#eda100", "#4a3aa7", "#e87ba4",
  "#e34948", "#199e70", "#d95926", "#9085e9", "#888781",
];

// Refine effort -> (loop budget, FreeRouting passes per route). Higher = slower
// but more chances to close the routing gap.
const EFFORT = {
  quick: { budget: 3, passes: 10 },
  normal: { budget: 5, passes: 20 },
  thorough: { budget: 10, passes: 30 },
};
function blockColor(block, blockList) {
  const i = blockList.indexOf(block);
  return BLOCK_COLORS[(i < 0 ? 0 : i) % BLOCK_COLORS.length];
}

// Build the inner SVG (outline + footprint rects) for a board geometry.
// `labels` adds the refdes text (skipped for tiny gallery thumbnails).
function boardSvgMarkup(geom, { labels = true } = {}) {
  const o = geom.outline;
  const W = o.x1 - o.x0;
  const H = o.y1 - o.y0;
  const blockList = [...new Set(geom.footprints.map((f) => f.block))].sort();
  const parts = geom.footprints
    .map((f) => {
      const x = f.x - f.w / 2 - o.x0;
      const y = f.y - f.h / 2 - o.y0;
      const conn = state.connectors.has(f.ref);
      const col = blockColor(f.block, blockList);
      const text = labels
        ? `<text x="${(x + 0.3).toFixed(2)}" y="${(y + 2).toFixed(2)}">${f.ref}</text>`
        : "";
      return (
        `<g class="fp${conn ? " conn" : ""}" data-ref="${f.ref}">` +
        `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(f.w, 0.5).toFixed(2)}" ` +
        `height="${Math.max(f.h, 0.5).toFixed(2)}" fill="${col}" fill-opacity="0.5" stroke="${col}"/>` +
        text +
        `</g>`
      );
    })
    .join("");
  const inner =
    `<rect x="0" y="0" width="${W.toFixed(1)}" height="${H.toFixed(1)}" fill="none" stroke="#333"/>` +
    parts;
  return { W, H, inner };
}

function renderBoard(geom) {
  const host = $("boardCanvas");
  const { W, H, inner } = boardSvgMarkup(geom, { labels: true });
  host.innerHTML =
    `<svg viewBox="0 0 ${W.toFixed(1)} ${H.toFixed(1)}">` + inner + `</svg>`;
  host.querySelectorAll(".fp").forEach((g) => {
    g.addEventListener("click", () => toggleConnector(g.dataset.ref));
  });
  updateConnCount();
}

function updateConnCount() {
  $("connCount").textContent = `${state.connectors.size} connectors`;
}

async function toggleConnector(ref) {
  if (state.connectors.has(ref)) state.connectors.delete(ref);
  else state.connectors.add(ref);
  await window.api.saveConnectors({
    board: state.board,
    connectors: [...state.connectors],
  });
  renderBoard(state.geometry);
}

async function loadBoardView() {
  if (!state.python || !state.board) return;
  $("boardMode").textContent = "loading…";
  const res = await window.api.dumpBoard({
    python: state.python,
    board: state.board,
  });
  if (!res.ok) {
    $("boardMode").textContent = "could not render board";
    appendLog("dump error: " + res.error);
    return;
  }
  state.geometry = res.geometry;
  const saved = await window.api.loadConnectors({ board: state.board });
  state.connectors = new Set(
    saved ||
      res.geometry.footprints
        .filter((f) => f.is_connector_guess)
        .map((f) => f.ref)
  );
  $("boardView").hidden = false;
  $("boardMode").textContent = "before placement";
  renderBoard(state.geometry);
}

const state = {
  python: null, // verified KiCad python path
  board: null, // selected .kicad_pcb
  running: false,
  geometry: null,
  connectors: new Set(),
  refineToolsOk: false, // Java + FreeRouting jar present
  candidates: [], // [{seed, hpwl_mm, crossings, hpwl_delta_pct, board}]
  committedSeed: null, // seed of the candidate the user picked
  refineBoard: null, // board Refine should operate on (the committed output)
  lastFinished: null, // best guess at the finished routed board (for Finalize)
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
  loadBoardView();
}

function refreshRunEnabled() {
  const ready = state.python && state.board && !state.running;
  $("run").disabled = !ready;
  const refineBtn = $("refine");
  if (refineBtn) refineBtn.disabled = !ready || !state.refineToolsOk;
  const fin = $("finalize");
  if (fin) fin.disabled = !ready;
  const cancel = $("cancel");
  if (cancel) cancel.hidden = !state.running;       // only visible mid-run
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
      place: "Placing candidates…",
      analyze: "Analyzing connectivity…",
      seed: "Seeding layout…",
      anneal: "Optimizing placement…",
      legalize: "Removing overlaps…",
      route: "Routing with FreeRouting…",
      refine: "Routing + refining…",
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
  const fab = report.fab ? ` · ${report.fab}` : "";
  badge.textContent =
    `${report.overlaps_remaining === 0 ? "overlap-free" : "needs review"} · seed ${report.seed}${fab}`;
}

// ---- candidate gallery -----------------------------------------------------
function resetGallery() {
  state.candidates = [];
  $("galleryGrid").innerHTML = "";
  $("gallery").hidden = false;
  $("galleryNote").textContent = "generating candidates…";
}

function addCandidateCard(cand) {
  state.candidates.push(cand);
  const grid = $("galleryGrid");
  const card = document.createElement("div");
  card.className = "cand";
  card.dataset.seed = String(cand.seed);
  const { W, H, inner } = boardSvgMarkup(cand.board, { labels: false });
  const delta =
    cand.hpwl_delta_pct === null || cand.hpwl_delta_pct === undefined
      ? ""
      : `<span class="cand-delta ${cand.hpwl_delta_pct < 0 ? "delta-good" : "delta-bad"}">` +
        `${cand.hpwl_delta_pct > 0 ? "+" : ""}${cand.hpwl_delta_pct}%</span>`;
  card.innerHTML =
    `<div class="cand-thumb"><svg viewBox="0 0 ${W.toFixed(1)} ${H.toFixed(1)}">${inner}</svg></div>` +
    `<div class="cand-meta">` +
    `<span class="cand-seed">seed ${cand.seed}</span>` +
    `<span class="cand-badge" hidden>best</span>` +
    `</div>` +
    `<div class="cand-metrics">` +
    `${fmt(Math.round(cand.hpwl_mm))} mm ${delta} · ${fmt(cand.crossings)} crossings</div>`;
  card.addEventListener("click", () => commitSeed(cand.seed));
  grid.appendChild(card);
  markBestCandidate();
}

function addCandidateError(cand) {
  const grid = $("galleryGrid");
  const card = document.createElement("div");
  card.className = "cand cand-failed";
  card.innerHTML =
    `<div class="cand-thumb cand-thumb-empty">✕</div>` +
    `<div class="cand-meta"><span class="cand-seed">seed ${cand.seed}</span></div>` +
    `<div class="cand-metrics">failed: ${cand.error || "placement error"}</div>`;
  grid.appendChild(card);
}

function markBestCandidate() {
  const ok = state.candidates;
  if (!ok.length) return;
  let best = ok[0];
  for (const c of ok) if (c.hpwl_mm < best.hpwl_mm) best = c;
  $("galleryGrid")
    .querySelectorAll(".cand-badge")
    .forEach((b) => (b.hidden = true));
  const card = $("galleryGrid").querySelector(`.cand[data-seed="${best.seed}"]`);
  if (card) {
    const badge = card.querySelector(".cand-badge");
    if (badge) badge.hidden = false;
  }
}

async function run() {
  if (state.running) return;
  state.running = true;
  $("cancel").disabled = false;
  refreshRunEnabled();
  $("results").hidden = true;
  $("log").textContent = "";
  resetGallery();
  setProgress("load", 0);

  const res = await window.api.runPlaceMulti({
    board: state.board,
    python: state.python,
    strategy: $("strategy").value,
    fab: $("fab").value,
    count: 6,
  });

  state.running = false;
  refreshRunEnabled();

  if (res.ok) {
    setProgress("done", 100);
    $("galleryNote").textContent = `${res.count} candidates — click one to use it`;
  } else if (res.cancelled) {
    setProgress("done", 0);
    $("progressStage").textContent = "Cancelled";
    $("galleryNote").textContent = state.candidates.length
      ? `${state.candidates.length} candidates (cancelled) — click one to use it`
      : "cancelled";
  } else {
    setProgress("done", 100);
    $("progressStage").textContent = "Failed";
    $("galleryNote").textContent = "no candidates produced";
    appendLog("ERROR: " + res.error);
    openLog(true);
  }
}

// Commit a previewed candidate: re-run the single-seed place (deterministic, so
// the saved board matches the thumbnail), then show results + the chosen board.
async function commitSeed(seed) {
  if (state.running) return;
  state.running = true;
  $("cancel").disabled = false;
  refreshRunEnabled();
  $("galleryGrid")
    .querySelectorAll(".cand")
    .forEach((c) => c.classList.toggle("cand-selected", c.dataset.seed === String(seed)));
  $("results").hidden = true;
  setProgress("load", 0);
  $("progressStage").textContent = `Saving seed ${seed}…`;

  const res = await window.api.runPlace({
    board: state.board,
    python: state.python,
    strategy: $("strategy").value,
    fab: $("fab").value,
    seed,
  });

  state.running = false;
  refreshRunEnabled();

  if (res.ok) {
    setProgress("done", 100);
    state.committedSeed = seed;
    state.refineBoard = res.output;
    // carry the connector set next to the saved board so Refine keeps edge pins
    await window.api.saveConnectors({
      board: res.output,
      connectors: [...state.connectors],
    });
    showResults(res.report, res.output);
    const dump = await window.api.dumpBoard({ python: state.python, board: res.output });
    if (dump.ok) {
      state.geometry = dump.geometry;
      $("boardMode").textContent = `after placement · seed ${seed}`;
      renderBoard(state.geometry);
    }
  } else if (res.cancelled) {
    setProgress("done", 0);
    $("progressStage").textContent = "Cancelled";
  } else {
    setProgress("done", 100);
    $("progressStage").textContent = "Failed";
    appendLog("ERROR: " + res.error);
    openLog(true);
  }
}

async function runRefine() {
  if (state.running) return;
  const eff = EFFORT[$("effort").value] || EFFORT.normal;
  state.running = true;
  $("cancel").disabled = false;
  refreshRunEnabled();
  $("log").textContent = "";
  $("refineHistory").hidden = true;
  setProgress("refine", 0);
  $("progressStage").textContent = `Refining — up to ${eff.budget} iterations`;
  $("refineReadout").hidden = false;
  $("refinePct").textContent = "–";
  $("refineBest").textContent = "–";

  const res = await window.api.runRefine({
    board: state.refineBoard || state.board,
    python: state.python,
    seed: state.committedSeed ?? 0,
    fab: $("fab").value,
    budget: eff.budget,
    passes: eff.passes,
  });

  state.running = false;
  refreshRunEnabled();
  if (res.ok) {
    setProgress("done", 100);
    showResults(res.report, res.output);
    // the routed board is the natural "finished" board to finalize
    state.lastFinished = res.report.routed_output || res.output;
    $("refineBest").textContent = res.report.routed_pct;
    if (Array.isArray(res.report.history) && res.report.history.length) {
      $("refineHistory").textContent =
        "routed %: " + res.report.history.map((h) => (+h).toFixed(1)).join(" → ");
      $("refineHistory").hidden = false;
    }
    const dump = await window.api.dumpBoard({ python: state.python, board: res.output });
    if (dump.ok) {
      state.geometry = dump.geometry;
      $("boardMode").textContent = "after refinement";
      renderBoard(state.geometry);
    }
  } else if (res.cancelled) {
    setProgress("done", 0);
    $("progressStage").textContent = "Cancelled";
  } else {
    setProgress("done", 100);
    $("progressStage").textContent = "Refine failed";
    appendLog("ERROR: " + res.error);
    openLog(true);
  }
}

// ---- finalize --------------------------------------------------------------
// Promote a finished routed board to be the project's main .kicad_pcb and sweep
// the intermediates. Picks the finished file (default = last routed output),
// then the main process shows a native confirm before anything destructive.
async function finalizeProject() {
  if (state.running || !state.board) return;
  const finished = await window.api.pickBoard({
    title: "Select the finished routed board to finalize",
    defaultPath: state.lastFinished || state.output || state.board,
  });
  if (!finished) return;

  state.running = true;
  refreshRunEnabled();
  const res = await window.api.finalize({
    python: state.python,
    finished,
    project: state.board,
  });
  state.running = false;
  refreshRunEnabled();

  if (res.ok) {
    const r = res.result;
    appendLog(
      (r.promoted ? `Finalized → ${state.board}` : "Finalized (no promote)") +
        ` · deleted ${r.deleted.length} temp file${r.deleted.length === 1 ? "" : "s"}` +
        (r.backup ? ` · backup ${r.backup}` : "")
    );
    if (r.errors && r.errors.length) {
      appendLog("Could not delete: " + r.errors.map((e) => e.file).join(", "));
      openLog(true);
    }
    // the project board now holds the finished design — refresh the view
    state.lastFinished = null;
    state.output = null;
    $("results").hidden = true;
    await loadBoardView();
    $("boardMode").textContent = "finalized project board";
  } else if (res.cancelled) {
    appendLog("Finalize cancelled.");
  } else {
    appendLog("Finalize failed: " + res.error);
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
  else if (evt.type === "candidate") addCandidateCard(evt);
  else if (evt.type === "candidate-error") addCandidateError(evt);
  else if (evt.type === "done") {
    /* gallery completion handled by the run() resolve */
  } else if (evt.type === "iteration") {
    const budget = evt.budget || 1;
    setProgress("refine", Math.round((evt.iter / budget) * 100));
    $("progressStage").textContent = `Refining — iteration ${evt.iter}/${budget}`;
    $("refineReadout").hidden = false;
    $("refinePct").textContent = evt.routed_pct;
    $("refineBest").textContent = evt.best_pct;
  } else if (evt.type === "result") showResults(evt.report, evt.report.output);
  else if (evt.type === "log") appendLog(evt.line);
});

$("pickBoard").addEventListener("click", pickBoard);
$("run").addEventListener("click", run);
$("refine").addEventListener("click", runRefine);
$("finalize").addEventListener("click", finalizeProject);
$("cancel").addEventListener("click", async () => {
  $("cancel").disabled = true;
  $("progressStage").textContent = "Cancelling…";
  await window.api.cancelRun();
});
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
  // Refine needs Java + FreeRouting; gate the button and explain if missing.
  const tools = await window.api.checkRefineTools();
  state.refineToolsOk = tools.ok;
  if (!tools.ok) {
    const why = !tools.java
      ? "Java not found on PATH"
      : `FreeRouting jar missing (${tools.jarPath})`;
    $("refine").title = `Route-driven refine needs FreeRouting — ${why}`;
    $("refineNote").textContent = `Refine disabled: ${why}`;
    $("refineNote").hidden = false;
  }
  refreshRunEnabled();
  const dev = await window.api.devConfig();
  if (dev && dev.board) {
    state.board = dev.board;
    $("boardPath").textContent = dev.board;
    $("boardPath").classList.remove("muted");
    refreshRunEnabled();
    loadBoardView();
    if (dev.autorun && state.python) run();
  }
}

init();
