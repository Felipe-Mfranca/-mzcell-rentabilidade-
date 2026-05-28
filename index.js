/**
 * index.js — Electron Main Process
 * MZCell Rentabilidade
 */

const { app, BrowserWindow, shell, dialog } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const http = require('http');
const fs   = require('fs');

const { autoUpdater } = require('electron-updater');

autoUpdater.autoDownload = true;
autoUpdater.autoInstallOnAppQuit = true;

autoUpdater.on('update-available', (info) => {
  dialog.showMessageBox({
    type: 'info',
    title: 'Atualização disponível',
    message: `Nova versão ${info.version} disponível! Baixando automaticamente...`,
    buttons: ['OK']
  });
});

autoUpdater.on('update-downloaded', (info) => {
  dialog.showMessageBox({
    type: 'info',
    title: 'Atualização pronta',
    message: `Versão ${info.version} pronta! Reinicie para instalar.`,
    buttons: ['Reiniciar agora', 'Depois']
  }).then(result => {
    if (result.response === 0) autoUpdater.quitAndInstall();
  });
});

autoUpdater.on('error', (err) => {
  console.error('Erro no auto-updater:', err.message);
});

const PORT      = 8001;
const HOST      = '127.0.0.1';
const RESOURCES = app.isPackaged
  ? path.join(process.resourcesPath)
  : path.join(__dirname, '..');

let mainWindow  = null;
let backendProc = null;

// ─── BACKEND PYTHON ───────────────────────────────────────────────────────────
function iniciarBackend() {
  // Tenta python-embed primeiro (instalador), fallback para venv (dev)
  const pythonExe = fs.existsSync(path.join(RESOURCES, 'python-embed', 'python.exe'))
    ? path.join(RESOURCES, 'python-embed', 'python.exe')
    : path.join(RESOURCES, 'venv', 'Scripts', 'python.exe');
  const mainPy    = path.join(RESOURCES, 'main.py');

  console.log('[Backend] Python:', pythonExe);
  console.log('[Backend] main.py:', mainPy);

  // Verifica se o python existe antes de tentar subir
  if (!fs.existsSync(pythonExe)) {
    dialog.showErrorBox(
      'Erro — Python não encontrado',
      `Não foi possível encontrar:\n${pythonExe}\n\nReinstale o aplicativo ou execute como Administrador.`
    );
    app.quit();
    return;
  }

  backendProc = spawn(pythonExe, ['-m', 'uvicorn', 'main:app', '--host', HOST, '--port', String(PORT)], {
    cwd: RESOURCES,
    windowsHide: true,
    env: { ...process.env, PYTHONPATH: RESOURCES },
  });

  backendProc.stdout.on('data', d => console.log('[Python]', d.toString()));
  backendProc.stderr.on('data', d => console.error('[Python ERR]', d.toString()));
  backendProc.on('close', code => console.log('[Backend] Encerrado, código:', code));
}

// ─── AGUARDA BACKEND ──────────────────────────────────────────────────────────
function aguardarBackend(tentativas = 0) {
  if (tentativas > 40) {
    dialog.showErrorBox('Erro', 'Não foi possível iniciar o servidor.\nTente reinstalar o aplicativo como Administrador.');
    app.quit();
    return;
  }
  http.get(`http://${HOST}:${PORT}/`, res => {
    if (res.statusCode < 500) {
      console.log('[Backend] Pronto!');
      carregarApp();
    } else {
      setTimeout(() => aguardarBackend(tentativas + 1), 1000);
    }
  }).on('error', () => {
    setTimeout(() => aguardarBackend(tentativas + 1), 1000);
  });
}

// ─── JANELA PRINCIPAL ─────────────────────────────────────────────────────────
function criarJanela() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'MZCell Rentabilidade',
    backgroundColor: '#0d0f14',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    show: false,
  });

  mainWindow.loadURL('data:text/html,<html style="background:%230d0f14;display:flex;align-items:center;justify-content:center;height:100vh;font-family:sans-serif"><div style="text-align:center"><div style="color:%23f59e0b;font-size:22px;font-weight:700;margin-bottom:10px">MZCell Rentabilidade</div><div style="color:%236b7280;font-size:14px">Iniciando servidor...</div></div></html>');
  mainWindow.show();

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
}

function carregarApp() {
  mainWindow.loadURL(`http://${HOST}:${PORT}/`);
}

// ─── LIFECYCLE ────────────────────────────────────────────────────────────────
app.whenReady().then(() => {
  criarJanela();
  iniciarBackend();
  aguardarBackend();

  // Verificar atualizações automaticamente (apenas no app instalado)
  if (app.isPackaged) {
    setTimeout(() => autoUpdater.checkForUpdatesAndNotify(), 5000);
  }
});

app.on('window-all-closed', () => {
  if (backendProc) backendProc.kill();
  app.quit();
});

app.on('before-quit', () => {
  if (backendProc) backendProc.kill();
});
