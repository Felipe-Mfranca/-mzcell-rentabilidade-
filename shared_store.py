"""
shared_store.py — Banco compartilhado (Google Sheets via Apps Script), com cópia local e fila offline
MZCell Rentabilidade

Cada computador roda o próprio servidor; o que precisa ser visto por todos (comentários, posição dos anúncios,
marcação de clip) é gravado localmente e enviado a uma planilha. Nada aqui chama o Claude: é código fixo.

  shared_config.json  {"url": ".../exec", "token": "...", "intervalo_seg": 60}   (fora do GitHub)
  shared_fila.json    registros que ainda não subiram (sem internet, por exemplo)
  shared_estado.json  até onde cada coleção já foi lida

Protocolo (Apps Script em compartilhado/Code.gs): POST JSON {token, acao, colecao, ...} com acao ping, listar
(desde) ou gravar (registros). Registro: {chave, dados, atualizado_em (ISO), autor, removido}.
Conflito: vale o mais recente (atualizado_em). Uso: python -m shared_store --testar
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone

import httpx

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "shared_config.json")
FILA_PATH = os.path.join(BASE_DIR, "shared_fila.json")
ESTADO_PATH = os.path.join(BASE_DIR, "shared_estado.json")
COLECOES = ("comentarios", "posicoes", "clips")
_trava = threading.Lock()


class ErroCompartilhado(Exception):
    pass


def agora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _ler(path: str, padrao):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return padrao


def _gravar(path: str, dados):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False)
    os.replace(tmp, path)


def config() -> dict:
    return _ler(CONFIG_PATH, {})


def configurado() -> bool:
    c = config()
    return all(c.get(k) and not str(c[k]).startswith("COLE_AQUI") for k in ("url", "token"))


def _post(corpo: dict, timeout: int = 25) -> dict:
    c = config()
    try:
        r = httpx.post(c["url"], json={**corpo, "token": c["token"]}, timeout=timeout, follow_redirects=True)
        dados = r.json()
    except (httpx.HTTPError, ValueError, KeyError) as e:
        raise ErroCompartilhado(f"sem conexão com a planilha: {type(e).__name__}")
    if not dados.get("ok"):
        raise ErroCompartilhado(str(dados.get("erro") or "resposta inválida")[:120])
    return dados


def testar() -> str:
    if not configurado():
        raise ErroCompartilhado("shared_config.json não preenchido")
    _post({"acao": "ping"})
    return "conectado"


# ─── envio (com fila) ─────────────────────────────────────────────────────────

def registro(chave: str, dados, autor: str = "", removido: bool = False, ts: str = None) -> dict:
    return {"chave": chave, "dados": dados if isinstance(dados, str) else json.dumps(dados, ensure_ascii=False),
            "atualizado_em": ts or agora_iso(), "autor": autor, "removido": bool(removido)}


def esvaziar_fila() -> int:
    """Envia o que está na fila; o que falhar continua nela. Devolve quantos subiram."""
    with _trava:
        fila = _ler(FILA_PATH, [])
        if not fila or not configurado():
            return 0
        restante, subiram = [], 0
        for col in COLECOES:
            lote = [x for x in fila if x["colecao"] == col]
            if not lote:
                continue
            try:
                _post({"acao": "gravar", "colecao": col, "registros": [x["registro"] for x in lote]})
                subiram += len(lote)
            except ErroCompartilhado:
                restante += lote
        restante += [x for x in fila if x["colecao"] not in COLECOES]
        _gravar(FILA_PATH, restante)
        return subiram


def enviar(colecao: str, chave: str, dados, autor: str = "", removido: bool = False) -> bool:
    """Coloca na fila e tenta subir já. Devolve True se subiu; False se ficou na fila (offline ou sem configuração)."""
    if colecao not in COLECOES or not configurado():
        return False
    with _trava:
        fila = _ler(FILA_PATH, [])
        fila.append({"colecao": colecao, "registro": registro(chave, dados, autor, removido)})
        _gravar(FILA_PATH, fila)
    esvaziar_fila()
    return not _ler(FILA_PATH, [])


def enviar_async(*args, **kwargs):
    """Mesmo que `enviar`, sem travar quem chamou (a tela não espera a planilha)."""
    threading.Thread(target=lambda: enviar(*args, **kwargs), daemon=True).start()


# ─── leitura ──────────────────────────────────────────────────────────────────

def puxar(colecao: str) -> list:
    """Registros alterados desde a última leitura (atualiza o marcador só depois de ler com sucesso)."""
    estado = _ler(ESTADO_PATH, {})
    r = _post({"acao": "listar", "colecao": colecao, "desde": estado.get(colecao, "")})
    registros = r.get("registros", [])
    if registros:
        estado[colecao] = max(x["atualizado_em"] for x in registros)
        _gravar(ESTADO_PATH, estado)
    return registros


def sincronizar(aplicadores: dict) -> dict:
    """Sobe a fila e aplica o que veio da planilha. `aplicadores`: {coleção: função(lista_de_registros)}."""
    if not configurado():
        return {"configurado": False}
    resumo = {"configurado": True, "subiram": esvaziar_fila(), "recebidos": {}}
    for col, aplicar in aplicadores.items():
        try:
            regs = puxar(col)
            if regs:
                aplicar(regs)
            resumo["recebidos"][col] = len(regs)
        except ErroCompartilhado as e:
            resumo["erro"] = str(e)
            break
    return resumo


def iniciar_sincronizacao(aplicadores: dict):
    """Thread de fundo: sincroniza ao iniciar e depois a cada `intervalo_seg`."""
    if not configurado():
        return None

    def laco():
        while True:
            try:
                sincronizar(aplicadores)
            except Exception:
                pass          # nunca derruba o servidor por causa da planilha
            time.sleep(max(15, int(config().get("intervalo_seg") or 60)))

    t = threading.Thread(target=laco, daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    if "--testar" in sys.argv:
        try:
            print(testar())
        except ErroCompartilhado as e:
            print(f"ERRO: {e}")
            sys.exit(1)
    else:
        print(__doc__)
