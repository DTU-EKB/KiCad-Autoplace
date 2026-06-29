"use strict";
// The only bridge between the sandboxed renderer and the main process.
// Exposes a minimal, explicit API -- no Node, no ipcRenderer, no fs.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("api", {
  detectPython: () => ipcRenderer.invoke("detect-python"),
  pickPython: () => ipcRenderer.invoke("pick-python"),
  pickBoard: () => ipcRenderer.invoke("pick-board"),
  runPlace: (opts) => ipcRenderer.invoke("run-place", opts),
  revealPath: (p) => ipcRenderer.invoke("reveal-path", p),
  devConfig: () => ipcRenderer.invoke("dev-config"),
  // streaming events from a running placement (progress / result / log)
  onPlaceEvent: (cb) => {
    const handler = (_e, data) => cb(data);
    ipcRenderer.on("place-event", handler);
    return () => ipcRenderer.removeListener("place-event", handler);
  },
});
