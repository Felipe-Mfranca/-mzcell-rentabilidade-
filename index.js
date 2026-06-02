const { app, BrowserWindow, dialog } = require('electron');
const { spawn, execSync } = require('child_process');
const path = require('path');
const http = require('http');
const https = require('https');
const fs = require('fs');

const PORT = 8001;
const HOST = '127.0.0.1';
const RESOURCES = app.isPackaged
  ? path.join(process.resourcesPath)
  : path.join(__dirname, '..');

const GITHUB_RAW = 'https://raw.githubusercontent.com/Felipe-Mfranca/-mzcell-rentabilidade-/main';
const ARQUIVOS_ONLINE = [
  'dashboard.html','main.py','ml_api.py','sync_produto.py',
  'parser_rentabilidade.py','sync_service.py','refresh_ml_token.py','ranking_ml.py',
  'auth_service.py'
];

let mainWindow = null;
let backendProc = null;
let houve_atualizacao = false;

function criarJanela() {
  mainWindow = new BrowserWindow({
    width: 1400, height: 900,
    backgroundColor: '#0a0a0f',
    show: false,
    webPreferences: { nodeIntegration: false, contextIsolation: true }
  });
  mainWindow.loadURL(`data:text/html,<!DOCTYPE html><html><head><style>
    *{margin:0;padding:0;box-sizing:border-box;}
    body{background:%230a0a0f;display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;font-family:'Segoe UI',sans-serif;}
    h1{color:%23f6c90e;font-size:28px;font-weight:700;margin-bottom:6px;letter-spacing:-.5px;}
    p{color:%239ca3af;font-size:13px;margin-bottom:40px;}
    .bar-bg{width:300px;height:4px;background:%231a1a24;border-radius:99px;overflow:hidden;}
    .bar{height:100%;background:%23f6c90e;border-radius:99px;transition:width .4s ease;width:0%;}
    .status{color:%236b7280;font-size:11px;margin-top:12px;letter-spacing:.02em;}
  </style></head><body>
    <h1>MZCell Rentabilidade</h1>
    <p>Mercado Livre &middot; An&aacute;lise em tempo real</p>
    <div class="bar-bg"><div class="bar" id="bar"></div></div>
    <div class="status" id="status">Verificando atualizações...</div>
    <script>window.setProgress=(p,m)=>{document.getElementById("bar").style.width=p+"%";document.getElementById("status").textContent=m;}</script>
  </body></html>`);
  mainWindow.show();
}

function setProgress(pct, msg) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.executeJavaScript(
      `window.setProgress(${pct},'${msg.replace(/'/g,"\\'")}')`
    ).catch(()=>{});
  }
}

function downloadArquivo(url, destPath) {
  return new Promise((resolve, reject) => {
    const tmp = destPath + '.tmp';
    const file = fs.createWriteStream(tmp);
    https.get(url, { rejectUnauthorized: false }, res => {
      if (res.statusCode === 301 || res.statusCode === 302) {
        file.close(); try{fs.unlinkSync(tmp);}catch(e){}
        return downloadArquivo(res.headers.location, destPath).then(resolve).catch(reject);
      }
      if (res.statusCode !== 200) {
        file.close(); try{fs.unlinkSync(tmp);}catch(e){}
        return reject(new Error('HTTP ' + res.statusCode));
      }
      res.pipe(file);
      file.on('finish', () => { file.close(); fs.renameSync(tmp, destPath); resolve(); });
    }).on('error', err => { file.close(); try{fs.unlinkSync(tmp);}catch(e){} reject(err); });
  });
}

async function verificarEAtualizar() {
  setProgress(5, 'Verificando atualizações...');
  let versaoOnline = null;
  try {
    versaoOnline = await new Promise((resolve, reject) => {
      https.get(GITHUB_RAW + '/version.txt', { rejectUnauthorized: false }, res => {
        if (res.statusCode !== 200) { res.resume(); return reject(new Error('HTTP ' + res.statusCode)); }
        let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d.trim()));
      }).on('error', reject);
    });
  } catch(e) {
    setProgress(100, 'Iniciando servidor...'); return;
  }
  const versionPath = path.join(RESOURCES, 'version.txt');
  let versaoLocal = '';
  try { versaoLocal = fs.readFileSync(versionPath, 'utf8').trim(); } catch(e) {}
  if (versaoOnline === versaoLocal) {
    setProgress(100, 'Iniciando servidor...'); return;
  }
  houve_atualizacao = true;
  setProgress(10, `Atualizando para versão ${versaoOnline}...`);
  for (let i = 0; i < ARQUIVOS_ONLINE.length; i++) {
    const arquivo = ARQUIVOS_ONLINE[i];
    const pct = 10 + Math.round((i / ARQUIVOS_ONLINE.length) * 80);
    setProgress(pct, `Baixando ${arquivo}...`);
    try {
      await downloadArquivo(GITHUB_RAW + '/' + arquivo, path.join(RESOURCES, arquivo));
    } catch(e) { console.error('Erro ao baixar ' + arquivo + ':', e.message); }
  }
  fs.writeFileSync(versionPath, versaoOnline, 'utf8');
  setProgress(95, 'Atualização concluída!');
}

async function liberarPorta() {
  try {
    const saida = execSync(`netstat -ano | findstr :${PORT}`, { encoding: 'utf8' });
    const pids = new Set();
    for (const linha of saida.split('\n')) {
      const col = linha.trim().split(/\s+/);
      const pid = col[col.length - 1];
      if (pid && /^\d+$/.test(pid) && pid !== '0') pids.add(pid);
    }
    for (const pid of pids) {
      try { execSync(`taskkill /PID ${pid} /F`, { encoding: 'utf8' }); } catch (_) {}
    }
    if (pids.size > 0) await new Promise(r => setTimeout(r, 500));
  } catch (_) {}
}

function iniciarBackend() {
  const pythonExe = fs.existsSync(path.join(RESOURCES, 'python-embed', 'python.exe'))
    ? path.join(RESOURCES, 'python-embed', 'python.exe')
    : path.join(RESOURCES, 'venv', 'Scripts', 'python.exe');
  if (!fs.existsSync(pythonExe)) {
    dialog.showErrorBox('Erro', 'Python não encontrado.\nTente reinstalar o aplicativo.');
    app.quit(); return;
  }
  backendProc = spawn(pythonExe, ['-m', 'uvicorn', 'main:app', '--host', HOST, '--port', PORT], {
    cwd: RESOURCES,
    env: { ...process.env, PYTHONPATH: RESOURCES }
  });
  const logPath = path.join(RESOURCES, 'server_error.log');
  const logStream = fs.createWriteStream(logPath, {flags:'w'});
  backendProc.stderr.on('data', d => { logStream.write(d); console.error('[backend]', d.toString()); });
  backendProc.stdout.on('data', d => { logStream.write(d); });
}

function aguardarBackend(tentativas = 0) {
  if (tentativas > 40) {
    const logPath = path.join(RESOURCES, 'server_error.log');
    let logMsg = '';
    try { logMsg = fs.readFileSync(logPath, 'utf8').slice(-500); } catch(e) {}
    dialog.showErrorBox('Erro ao iniciar servidor',
      'Não foi possível iniciar o servidor.\n\n' +
      'Possíveis causas:\n' +
      '- Porta 8001 em uso por outro programa\n' +
      '- Antivírus bloqueando o Python\n\n' +
      (logMsg ? 'Último erro:\n' + logMsg : 'Tente reiniciar o aplicativo.')
    );
    app.quit(); return;
  }
  http.get(`http://${HOST}:${PORT}/`, () => {
    mainWindow.loadURL(`http://${HOST}:${PORT}/`);
  }).on('error', () => setTimeout(() => aguardarBackend(tentativas + 1), 1000));
}

app.whenReady().then(async () => {
  criarJanela();
  await verificarEAtualizar();
  setProgress(100, 'Iniciando servidor...');
  if (houve_atualizacao) {
    await dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'MZ Rentabilidade v1.1.3 — atualizado ✓',
      message: 'Novidades desta versão:',
      detail: '• Receita bruta agora alinhada com o Seller Center do ML\n• Pedidos internos do ML não inflam mais os números de vendas\n• Validado em múltiplos anúncios e períodos',
      buttons: ['Entendi, continuar'],
      defaultId: 0
    }).catch(() => {});
  }
  await liberarPorta();
  iniciarBackend();
  aguardarBackend();
});

app.on('window-all-closed', () => {
  if (backendProc) backendProc.kill();
  if (process.platform !== 'darwin') app.quit();
});
