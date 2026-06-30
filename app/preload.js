"use strict";
// The only bridge between the sandboxed renderer and the main process.
// Exposes a minimal, explicit API -- no Node, no ipcRenderer, no fs.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  detectPython: () => ipcRenderer.invoke("detect-python"),
  pickPython: () => ipcRenderer.invoke("pick-python"),
  pickBoard: (opts) => ipcRenderer.invoke("pick-board", opts),
  runPlace: (opts) => ipcRenderer.invoke("run-place", opts),
  runPlaceMulti: (opts) => ipcRenderer.invoke("run-place-multi", opts),
  runRefine: (opts) => ipcRenderer.invoke("run-refine", opts),
  cancelRun: () => ipcRenderer.invoke("cancel-run"),
  finalize: (opts) => ipcRenderer.invoke("finalize", opts),
  preflight: (opts) => ipcRenderer.invoke("preflight", opts),
  checkRefineTools: () => ipcRenderer.invoke("check-refine-tools"),
  revealPath: (p) => ipcRenderer.invoke("reveal-path", p),
  devConfig: () => ipcRenderer.invoke("dev-config"),
  dumpBoard: (opts) => ipcRenderer.invoke("dump-board", opts),
  loadConnectors: (opts) => ipcRenderer.invoke("load-connectors", opts),
  saveConnectors: (opts) => ipcRenderer.invoke("save-connectors", opts),
  // streaming events from a running placement (progress / result / log)
  onPlaceEvent: (cb) => {
    const handler = (_e, data) => cb(data);
    ipcRenderer.on("place-event", handler);
    return () => ipcRenderer.removeListener("place-event", handler);
  },
});
