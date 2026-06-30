"use strict";

const $ = (id) => document.getElementById(id);

const BLOCK_COLORS = [
  "#2a78d6", "#1baf7a", "#eda100", "#4a3aa7", "#e87ba4",
  "#e34948", "#199e70", "#d95926", "#9085e9", "#888781",
];

const EFFORT = {
  quick: { budget: 3, passes: 10 },
  normal: { budget: 5, passes: 20 },
  thorough: { budget: 10, passes: 30 },
};

function blockColor(block, blockList) {
  const i = blockList.indexOf(block);
  return BLOCK_COLORS[(i < 0 ? 0 : i) % BLOCK_COLORS.length];
}

const state = {
  python: null,
  board: null,
  running: false,
  geometry: null,
  connectors: new Set(),
  lockedFootprints: new Set(),
  refineToolsOk: false,
  candidates: [],
  committedSeed: null,
  refineBoard: null,
  lastFinished: null,
  history: [], // [{ id, label, date, metric }]
  historyCounter: 0
};

// ---- SVG / Dragging State ----
let dragState = null; // { element, ref, startX, startY, origX, origY, scale }

function getSvgCoords(svg, evt) {
  const pt = svg.createSVGPoint();
  pt.x = evt.clientX;
  pt.y = evt.clientY;
  return pt.matrixTransform(svg.getScreenCTM().inverse());
}

function handleDragStart(evt) {
  if (evt.button !== 0) return; // Only left click for dragging
  const g = evt.target.closest('.fp');
  if (!g) return;
  const ref = g.dataset.ref;
  // If we just want to click, we will wait for mouseup to distinguish.
  const svg = $("boardCanvas").querySelector('svg');
  if (!svg) return;
  const pt = getSvgCoords(svg, evt);
  const fp = state.geometry.footprints.find(f => f.ref === ref);
  if (!fp) return;

  dragState = {
    element: g,
    ref: ref,
    startX: pt.x,
    startY: pt.y,
    origX: fp.x,
    origY: fp.y,
    moved: false,
    rect: g.querySelector('rect'),
    text: g.querySelector('text')
  };
  g.style.cursor = 'grabbing';
}

function handleDragMove(evt) {
  if (!dragState) return;
  const svg = $("boardCanvas").querySelector('svg');
  const pt = getSvgCoords(svg, evt);
  const dx = pt.x - dragState.startX;
  const dy = pt.y - dragState.startY;
  
  if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5) {
    dragState.moved = true;
  }
  
  if (dragState.moved) {
    const o = state.geometry.outline;
    const fp = state.geometry.footprints.find(f => f.ref === dragState.ref);
    if (!fp) return;
    
    // Update visual
    let newX = dragState.origX + dx;
    let newY = dragState.origY + dy;
    
    // Convert to SVG coords
    const svgX = newX - fp.w / 2 - o.x0;
    const svgY = newY - fp.h / 2 - o.y0;
    
    dragState.rect.setAttribute('x', svgX.toFixed(2));
    dragState.rect.setAttribute('y', svgY.toFixed(2));
    if (dragState.text) {
      dragState.text.setAttribute('x', (svgX + 0.3).toFixed(2));
      dragState.text.setAttribute('y', (svgY + 2).toFixed(2));
    }
  }
}

function handleDragEnd(evt) {
  if (!dragState) return;
  const svg = $("boardCanvas").querySelector('svg');
  const pt = getSvgCoords(svg, evt);
  
  const dx = pt.x - dragState.startX;
  const dy = pt.y - dragState.startY;
  
  if (!dragState.moved) {
    // It was just a click -> toggle connector
    toggleConnector(dragState.ref);
  } else {
    // Commit move
    const fp = state.geometry.footprints.find(f => f.ref === dragState.ref);
    if (fp) {
      fp.x = dragState.origX + dx;
      fp.y = dragState.origY + dy;
      // We could lock it automatically when manually moved
      if (!state.lockedFootprints.has(dragState.ref)) {
        toggleLock(dragState.ref, false);
      }
    }
  }
  dragState.element.style.cursor = 'grab';
  dragState = null;
}

function handleRightClick(evt) {
  const g = evt.target.closest('.fp');
  if (!g) return;
  evt.preventDefault();
  toggleLock(g.dataset.ref, true);
}


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
      const locked = state.lockedFootprints.has(f.ref);
      const col = blockColor(f.block, blockList);
      const text = labels
        ? `<text x="${(x + 0.3).toFixed(2)}" y="${(y + 2).toFixed(2)}">${f.ref}</text>`
        : "";
      
      let classes = "fp";
      if (conn) classes += " conn";
      if (locked) classes += " locked";

      return (
        `<g class="${classes}" data-ref="${f.ref}">` +
        `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${Math.max(f.w, 0.5).toFixed(2)}" ` +
        `height="${Math.max(f.h, 0.5).toFixed(2)}" fill="${col}" fill-opacity="0.5" stroke="${col}"/>` +
        text +
        `</g>`
      );
    })
    .join("");
  const inner =
    `<rect x="0" y="0" width="${W.toFixed(1)}" height="${H.toFixed(1)}" fill="none" stroke="rgba(255,255,255,0.1)"/>` +
    parts;
  return { W, H, inner };
}

function renderBoard(geom) {
  const host = $("boardCanvas");
  const { W, H, inner } = boardSvgMarkup(geom, { labels: true });
  host.innerHTML =
    `<svg viewBox="0 0 ${W.toFixed(1)} ${H.toFixed(1)}">` + inner + `</svg>`;
  
  const svg = host.querySelector('svg');
  svg.addEventListener('mousedown', handleDragStart);
  svg.addEventListener('mousemove', handleDragMove);
  window.addEventListener('mouseup', handleDragEnd); // Catch outside drops
  svg.addEventListener('contextmenu', handleRightClick);

  updateFootprintStats();
}

function updateFootprintStats() {
  $("connCount").textContent = `${state.connectors.size} connectors`;
  $("lockedCount").textContent = `${state.lockedFootprints.size} locked`;
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

function toggleLock(ref, reRender = true) {
  if (state.lockedFootprints.has(ref)) state.lockedFootprints.delete(ref);
  else state.lockedFootprints.add(ref);
  if (reRender) renderBoard(state.geometry);
  else updateFootprintStats();
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
  // Default no locked footprints initially
  $("boardView").hidden = false;
  $("boardMode").textContent = "Before placement";
  renderBoard(state.geometry);
  loadPreflight();
}

async function loadPreflight() {
  if (!state.python || !state.board) return;
  const res = await window.api.preflight({
    python: state.python,
    board: state.board,
  });
  const panel = $("preflight");
  const list = $("preflightRows");
  if (!res.ok) {
    panel.hidden = true;
    return;
  }
  
  const getIcon = (status) => {
    if (status === 'ok') return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>';
    if (status === 'warn') return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path><line x1="12" y1="9" x2="12" y2="13"></line><line x1="12" y1="17" x2="12.01" y2="17"></line></svg>';
    return '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="15" y1="9" x2="9" y2="15"></line><line x1="9" y1="9" x2="15" y2="15"></line></svg>';
  };

  list.innerHTML = res.rows
    .map(
      (r) =>
        `<li class="pf-row pf-${r.status}">` +
        `<span class="pf-icon">${getIcon(r.status)}</span>` +
        `<div class="pf-content">` +
        `<span class="pf-label">${r.label}</span>` +
        `<span class="pf-detail">${r.detail}</span>` +
        `</div></li>`
    )
    .join("");
  panel.hidden = false;
}

// ---- Python ----
function setPython(info) {
  state.python = info && info.pythonPath;
  const pill = $("pythonPill");
  const text = $("pythonText");
  const change = $("changePython");
  change.hidden = false;
  pill.classList.remove("pill-ok", "pill-bad", "pill-wait");
  if (state.python) {
    pill.classList.add("pill-ok");
    text.textContent = info.kicadVersion ? `KiCad ${info.kicadVersion}` : "KiCad ready";
    text.title = state.python;
  } else {
    pill.classList.add("pill-bad");
    text.textContent = "Python missing";
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

// ---- Board Picker ----
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
  if (cancel) cancel.hidden = !state.running;
}

// ---- Progress ----
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
  $("mBlocks").textContent = `${report.blocks} block${report.blocks === 1 ? "" : "s"}`;

  $("outPath").textContent = output;
  $("projNote").textContent = report.project_copied ? "· net-class rules carried over" : "";
  state.output = output;

  const badge = $("resultBadge");
  const fab = report.fab ? ` · ${report.fab}` : "";
  const overlaps = report.overlaps_remaining === 0 ? "overlap-free" : "needs review";
  badge.textContent = `${overlaps} · seed ${report.seed}${fab}`;
  badge.className = `badge ${report.overlaps_remaining === 0 ? 'badge-success' : 'badge-warning'}`;
}

// ---- Candidates ----
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
  const spread = cand.sheet_spread_score === undefined ? "—" : cand.sheet_spread_score.toFixed(2);
  const pinch = cand.pinch_fraction === undefined ? "—" : `${Math.round(cand.pinch_fraction * 100)}%`;
  const ws = cand.whitespace_connectivity === undefined ? "—" : `${Math.round(cand.whitespace_connectivity * 100)}%`;
  card.innerHTML =
    `<div class="cand-thumb"><svg viewBox="0 0 ${W.toFixed(1)} ${H.toFixed(1)}">${inner}</svg></div>` +
    `<div class="cand-meta">` +
    `<span class="cand-seed">seed ${cand.seed}</span>` +
    `<span class="badge badge-success cand-badge" hidden>best</span>` +
    `</div>` +
    `<div class="cand-metrics">` +
    `<div class="cand-metrics-row">${fmt(Math.round(cand.hpwl_mm))} mm ${delta} · ${fmt(cand.crossings)} crossings</div>` +
    `<div class="cand-metrics-row cand-metrics-proxy">spread ${spread} · pinch ${pinch} · ws ${ws} · overlaps ${fmt(cand.overlaps)}</div>` +
    `</div>`;
  card.addEventListener("click", () => commitSeed(cand.seed));
  grid.appendChild(card);
  markBestCandidate();
}

function addCandidateError(cand) {
  const grid = $("galleryGrid");
  const card = document.createElement("div");
  card.className = "cand cand-failed";
  card.innerHTML =
    `<div class="cand-thumb cand-thumb-empty"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg></div>` +
    `<div class="cand-meta"><span class="cand-seed">seed ${cand.seed}</span></div>` +
    `<div class="cand-metrics text-danger">failed: ${cand.error || "placement error"}</div>`;
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

// ---- Run Flow ----
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
    await window.api.saveConnectors({
      board: res.output,
      connectors: [...state.connectors],
    });
    showResults(res.report, res.output);
    
    // Add to history
    addHistoryEntry(`Seed ${seed}`, res.report);

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

// ---- History ----
function addHistoryEntry(label, report) {
  state.historyCounter++;
  const entry = {
    id: state.historyCounter,
    label,
    hpwl: report.after.hpwl_mm,
    crossings: report.after.crossings
  };
  state.history.push(entry);
  updateHistoryUI();
}

function updateHistoryUI() {
  const panel = $("historyPanel");
  const list = $("historyList");
  const count = $("historyCount");
  if (state.history.length > 0) panel.hidden = false;
  count.textContent = state.history.length;
  
  list.innerHTML = state.history.slice().reverse().map(h => `
    <div class="history-item" data-id="${h.id}">
      <span class="font-medium text-main">${h.label}</span>
      <span class="muted">${fmt(Math.round(h.hpwl))}mm · ${h.crossings}x</span>
    </div>
  `).join('');
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
    sides: parseInt($("sides").value, 10) || 2,
    budget: eff.budget,
    passes: eff.passes,
  });

  state.running = false;
  refreshRunEnabled();
  if (res.ok) {
    setProgress("done", 100);
    showResults(res.report, res.output);
    state.lastFinished = res.report.routed_output || res.output;
    $("refineBest").textContent = res.report.routed_pct;
    if (Array.isArray(res.report.history) && res.report.history.length) {
      $("refineHistory").textContent =
        "routed %: " + res.report.history.map((h) => (+h).toFixed(1)).join(" → ");
      $("refineHistory").hidden = false;
    }
    
    addHistoryEntry(`Refined`, res.report);

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

function openLog(force) {
  const log = $("log");
  const btn = $("logToggle");
  const open = force === undefined ? log.hidden : force;
  log.hidden = !open;
  btn.classList.toggle("open", open);
}

// ---- Event Listeners ----

$("settingsToggle").addEventListener("click", () => {
  const content = $("settingsContent");
  const header = $("settingsToggle");
  const isOpen = content.classList.contains("open");
  if (isOpen) {
    content.classList.remove("open");
    header.classList.remove("open");
  } else {
    content.classList.add("open");
    header.classList.add("open");
  }
});

$("exportSvgBtn").addEventListener("click", () => {
  const svgWrapper = $("boardCanvas").innerHTML;
  const blob = new Blob([svgWrapper], {type: "image/svg+xml"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "autoplace_board_layout.svg";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

window.api.onPlaceEvent((evt) => {
  if (evt.type === "progress") setProgress(evt.stage, evt.percent);
  else if (evt.type === "candidate") addCandidateCard(evt);
  else if (evt.type === "candidate-error") addCandidateError(evt);
  else if (evt.type === "iteration") {
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
  const tools = await window.api.checkRefineTools();
  state.refineToolsOk = tools.ok;
  if (!tools.ok) {
    const why = !tools.java ? "Java not found on PATH" : `FreeRouting missing (${tools.jarPath})`;
    $("refine").title = `Refine needs FreeRouting — ${why}`;
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
