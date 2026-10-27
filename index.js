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
  'auth_service.py',
  'reposicao_full.py',
  'cmv_base.py',
  'pivo_ml.py',
  'full_alerta.py',
  'shared_store.py',
  'rebot_agenda.py',
  'rebot_visual.py',
  'buybox_monitor.py',
  'marketplaces/__init__.py',
  'marketplaces/amazon/__init__.py',
  'marketplaces/amazon/api.py',
  'marketplaces/amazon/calculos.py',
  'marketplaces/amazon/conferencia.py',
  'marketplaces/amazon/custos_xlsx.py',
  'marketplaces/amazon/diagnostico.py',
  'marketplaces/amazon/financeiro.py',
  'marketplaces/amazon/marketplace.py',
  'marketplaces/amazon/painel.py',
  'marketplaces/amazon/relatorios.py',
  'marketplaces/amazon/rentabilidade.py',
  'marketplaces/amazon/reposicao.py',
  'marketplaces/amazon/router.py',
  'marketplaces/amazon/validar.py',
  'marketplaces/base.py',
  'marketplaces/bling/__init__.py',
  'marketplaces/bling/api.py',
  'marketplaces/bling/auth.py',
  'marketplaces/expedicao/__init__.py',
  'marketplaces/expedicao/ml.py',
  'marketplaces/expedicao/pagina.html',
  'marketplaces/expedicao/router.py',
  'marketplaces/shopee/__init__.py',
  'marketplaces/shopee/api.py',
  'marketplaces/shopee/lojas.py',
  'marketplaces/shopee/marketplace.py',
  'marketplaces/shopee/router.py',
  'marketplaces/shopee/validar.py',
  'impostos.py',
  'marketplaces/amazon/ads.py',
  'marketplaces/amazon/promocoes.py',
  'produtos_store.py',
  'marketplaces/shopee/precificador.py'
];
// Arquivo novo não precisa mais de instalador: a lista também vem do GitHub (arquivos_online.json) e é somada a esta.
// Só caminhos relativos de .py/.html do projeto; nunca config, token ou dados (esses ficam só na máquina).
const LISTA_ONLINE = 'arquivos_online.json';
const caminhoPermitido = a => typeof a === 'string' && /^[A-Za-z0-9_\-]+(\/[A-Za-z0-9_\-]+)*\.(py|html)$/.test(a);

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
    <h1>MZ App</h1>
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
    try { fs.mkdirSync(path.dirname(destPath), { recursive: true }); } catch (e) {}
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

function baixarTexto(url) {
  return new Promise((resolve, reject) => {
    https.get(url, { rejectUnauthorized: false }, res => {
      if (res.statusCode !== 200) { res.resume(); return reject(new Error('HTTP ' + res.statusCode)); }
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve(d));
    }).on('error', reject);
  });
}

function versaoMaior(a, b) {
  const pa = String(a || '').split('.').map(n => parseInt(n, 10) || 0);
  const pb = String(b || '').split('.').map(n => parseInt(n, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const x = pa[i] || 0, y = pb[i] || 0;
    if (x !== y) return x > y;
  }
  return false;
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
  // Só atualiza quando a versão do repositório é MAIOR que a instalada: nunca rebaixa
  if (!versaoMaior(versaoOnline, versaoLocal)) {
    setProgress(100, 'Iniciando servidor...'); return;
  }
  houve_atualizacao = true;
  setProgress(10, `Atualizando para versão ${versaoOnline}...`);
  // lista embutida + lista do GitHub (se o arquivo da lista falhar, segue só com a embutida)
  const arquivos = new Set(ARQUIVOS_ONLINE);
  try {
    const extra = JSON.parse(await baixarTexto(GITHUB_RAW + '/' + LISTA_ONLINE));
    (Array.isArray(extra) ? extra : []).filter(caminhoPermitido).forEach(a => arquivos.add(a));
  } catch(e) { console.error('Lista online indisponível:', e.message); }
  const lista = [...arquivos];
  for (let i = 0; i < lista.length; i++) {
    const arquivo = lista[i];
    const pct = 10 + Math.round((i / lista.length) * 80);
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
  const logPath = path.join(app.getPath('userData'), 'server_error.log');
  const logStream = fs.createWriteStream(logPath, {flags:'w'});
  backendProc.stderr.on('data', d => { logStream.write(d); console.error('[backend]', d.toString()); });
  backendProc.stdout.on('data', d => { logStream.write(d); });
}

function aguardarBackend(tentativas = 0) {
  if (tentativas > 40) {
    const logPath = path.join(app.getPath('userData'), 'server_error.log');
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
      title: 'MZ App v1.1.3 — atualizado ✓',
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
