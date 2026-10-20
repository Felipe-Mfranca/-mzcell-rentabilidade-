/**
 * MZ-app — banco compartilhado no Google Sheets (Apps Script).
 *
 * Cole este arquivo em: planilha > Extensões > Apps Script. Depois siga o compartilhado/LEIA-ME.md.
 * O código de acesso NÃO fica aqui: fica em Configurações do projeto > Propriedades do script > TOKEN.
 * Se o script for um projeto avulso (script.google.com), defina também a propriedade PLANILHA_ID (o trecho da URL
 * da planilha entre /d/ e /edit); presa à planilha (Extensões > Apps Script), ela é opcional.
 *
 * Cada coleção vira uma aba (criada sozinha): comentarios, posicoes, clips.
 * Colunas: chave | dados (JSON) | atualizado_em (ISO) | autor | removido
 * Regra de conflito: vale a gravação mais recente (compara atualizado_em).
 */
var COLECOES = ['comentarios', 'posicoes', 'clips'];
var CABECALHO = ['chave', 'dados', 'atualizado_em', 'autor', 'removido'];

function doPost(e) {
  try {
    var corpo = JSON.parse(e.postData.contents);
    var token = PropertiesService.getScriptProperties().getProperty('TOKEN');
    if (!token || corpo.token !== token) return saida({ ok: false, erro: 'nao autorizado' });
    if (corpo.acao === 'ping') return saida({ ok: true, agora: new Date().toISOString() });
    if (COLECOES.indexOf(corpo.colecao) < 0) return saida({ ok: false, erro: 'colecao invalida' });
    var aba = abaDe(corpo.colecao);
    if (corpo.acao === 'listar') return saida(listar(aba, corpo.desde));
    if (corpo.acao === 'gravar') return saida(gravar(aba, corpo.registros || []));
    return saida({ ok: false, erro: 'acao invalida' });
  } catch (err) {
    return saida({ ok: false, erro: String(err) });
  }
}

function doGet() {
  return saida({ ok: true, servico: 'MZ-app banco compartilhado' });
}

function saida(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function planilha() {
  var id = PropertiesService.getScriptProperties().getProperty('PLANILHA_ID');
  return id ? SpreadsheetApp.openById(id) : SpreadsheetApp.getActiveSpreadsheet();
}

function abaDe(nome) {
  var ss = planilha();
  var aba = ss.getSheetByName(nome);
  if (!aba) {
    aba = ss.insertSheet(nome);
    aba.appendRow(CABECALHO);
    aba.setFrozenRows(1);
    aba.getRange('A:E').setNumberFormat('@');   // texto puro: o Sheets não converte a data ISO nem o JSON
  }
  return aba;
}

function texto(v) {
  return v instanceof Date ? v.toISOString() : String(v == null ? '' : v);
}

function listar(aba, desde) {
  var v = aba.getDataRange().getValues();
  var registros = [];
  for (var i = 1; i < v.length; i++) {
    var atualizado = texto(v[i][2]);
    if (!desde || atualizado > desde) {
      registros.push({ chave: texto(v[i][0]), dados: texto(v[i][1]), atualizado_em: atualizado,
                       autor: texto(v[i][3]), removido: texto(v[i][4]) === 'true' });
    }
  }
  return { ok: true, registros: registros, agora: new Date().toISOString() };
}

function gravar(aba, registros) {
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var v = aba.getDataRange().getValues();
    var linhaDe = {}, atualDe = {};
    for (var i = 1; i < v.length; i++) { linhaDe[texto(v[i][0])] = i + 1; atualDe[texto(v[i][0])] = texto(v[i][2]); }
    var gravados = 0;
    registros.forEach(function (r) {
      var chave = String(r.chave);
      var linha = [chave, typeof r.dados === 'string' ? r.dados : JSON.stringify(r.dados), String(r.atualizado_em),
                   String(r.autor || ''), r.removido ? 'true' : 'false'];
      if (linhaDe[chave]) {
        if (String(r.atualizado_em) < atualDe[chave]) return;          // já existe uma gravação mais recente
        aba.getRange(linhaDe[chave], 1, 1, 5).setValues([linha]);
      } else {
        aba.appendRow(linha);
        linhaDe[chave] = aba.getLastRow();
      }
      atualDe[chave] = String(r.atualizado_em);
      gravados++;
    });
    return { ok: true, gravados: gravados };
  } finally {
    lock.releaseLock();
  }
}
