// preload.js — MZCell Rentabilidade
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  onBackendStatus: (callback) => ipcRenderer.on('backend-status', (_, status) => callback(status)),
});
