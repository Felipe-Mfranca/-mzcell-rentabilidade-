"""
atualizador.py — Atualização do MZ App pelo servidor da empresa (no lugar do GitHub)
MZ App

Servidor (pasta com .git): publica em /atualizacao/manifesto e /atualizacao/arquivo/... a versão COMMITADA (git HEAD)
dos mesmos arquivos que o instalador atualiza (lista do index.js + arquivos_online.json). Nada fora da lista sai,
nunca config, token ou dados.

PC instalado (sem .git): ao subir o backend, procura o servidor nas fontes de modelos/fontes_atualizacao.json
(ou "fontes_atualizacao" no mz_local.json). Se a versão de lá for maior, baixa só os arquivos que mudaram, confere o
SHA-256 de cada um e troca todos juntos no fim. Qualquer falha não troca nada. Vale ao reabrir o app.
"""

import hashlib
import json
import os
import re
import subprocess
import threading

FONTES_PADRAO = ["http://192.168.15.200:8001", "http://100.76.93.35:8001"]   # servidor na rede e pelo Tailscale
FONTES_PATH = os.path.join("modelos", "fontes_atualizacao.json")
PASTAS_RECURSOS = ("static", "modelos", "scripts", "docs")
_EXT_RECURSOS = re.compile(r"\.(js|css|json|svg|png|jpg|jpeg|ico|woff2?|ps1|md|txt|csv)$", re.I)
_trava = threading.Lock()
_cache = {}   # sha do HEAD -> manifesto


def caminho_permitido(a) -> bool:
    """Mesma regra do caminhoPermitido do index.js."""
    if not isinstance(a, str) or ".." in a:
        return False
    if re.fullmatch(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*\.(py|html)", a):
        return True
    return (a.split("/")[0] in PASTAS_RECURSOS and "/" in a
            and bool(re.fullmatch(r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*/[A-Za-z0-9_\-.]+\.[A-Za-z0-9]{1,5}", a))
            and bool(_EXT_RECURSOS.search(a)))


def versao_maior(a, b) -> bool:
    pa = [int(x) if x.isdigit() else 0 for x in str(a or "").split(".")]
    pb = [int(x) if x.isdigit() else 0 for x in str(b or "").split(".")]
    n = max(len(pa), len(pb))
    return (pa + [0] * n)[:n] > (pb + [0] * n)[:n]


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ─── servidor ────────────────────────────────────────────────────────────────

def _git(base_dir: str, *args) -> bytes:
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", base_dir, *args], capture_output=True, check=True, timeout=30).stdout


def _lista(base_dir: str) -> list:
    js = _git(base_dir, "show", "HEAD:index.js").decode("utf-8", "replace")
    m = re.search(r"const ARQUIVOS_ONLINE = \[(.*?)\];", js, re.S)
    nomes = re.findall(r"'([^']+)'", m.group(1)) if m else []
    try:
        nomes += json.loads(_git(base_dir, "show", "HEAD:arquivos_online.json"))
    except (subprocess.CalledProcessError, ValueError):
        pass
    return sorted({a for a in nomes if caminho_permitido(a)} | {"atualizador.py", FONTES_PATH.replace(os.sep, "/")})


def manifesto(base_dir: str) -> dict:
    """{versao, arquivos: {caminho: sha256}} do que está commitado no servidor."""
    head = _git(base_dir, "rev-parse", "HEAD").strip().decode()
    with _trava:
        if head in _cache:
            return _cache[head]
        arquivos = {}
        for a in _lista(base_dir):
            try:
                arquivos[a] = _sha(_git(base_dir, "show", f"HEAD:{a}"))
            except subprocess.CalledProcessError:
                continue   # está na lista mas não existe no repositório
        versao = _git(base_dir, "show", "HEAD:version.txt").decode().strip()
        _cache.clear()
        _cache[head] = {"versao": versao, "commit": head[:7], "arquivos": arquivos}
        return _cache[head]


def conteudo(base_dir: str, caminho: str):
    """Bytes commitados do arquivo, só se ele estiver no manifesto; senão None."""
    if caminho not in manifesto(base_dir)["arquivos"]:
        return None
    return _git(base_dir, "show", f"HEAD:{caminho}")


# ─── PC instalado ────────────────────────────────────────────────────────────

def _ler(path: str, padrao=""):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return padrao


def fontes(base_dir: str) -> list:
    for pasta in (os.path.join(os.environ.get("APPDATA", ""), "MZ App"), base_dir):
        try:
            v = json.load(open(os.path.join(pasta, "mz_local.json"), encoding="utf-8")).get("fontes_atualizacao")
            if isinstance(v, list) and v:
                return [str(x).rstrip("/") for x in v]
        except (OSError, ValueError, AttributeError):
            pass
    try:
        v = json.load(open(os.path.join(base_dir, FONTES_PATH), encoding="utf-8")).get("fontes")
        if isinstance(v, list) and v:
            return [str(x).rstrip("/") for x in v]
    except (OSError, ValueError, AttributeError):
        pass
    return list(FONTES_PADRAO)


def atualizar(base_dir: str, get=None) -> str:
    """Baixa a versão do servidor se for maior que a local. Devolve o que aconteceu (vai para o log)."""
    if os.path.exists(os.path.join(base_dir, ".git")):
        return "pasta com git (servidor ou desenvolvimento): atualiza pelo git"
    if get is None:
        import httpx
        get = lambda url: httpx.get(url, timeout=8)
    local = _ler(os.path.join(base_dir, "version.txt"))
    for fonte in fontes(base_dir):
        try:
            r = get(f"{fonte}/atualizacao/manifesto")
            if r.status_code != 200:
                continue
            man = r.json()
        except Exception:
            continue
        if not versao_maior(man.get("versao"), local):
            return f"em dia ({local}; {fonte} tem {man.get('versao')})"
        novos = {}
        for caminho, sha in (man.get("arquivos") or {}).items():
            if not caminho_permitido(caminho):
                continue
            destino = os.path.join(base_dir, *caminho.split("/"))
            try:
                if _sha(open(destino, "rb").read()) == sha:
                    continue
            except OSError:
                pass
            try:
                a = get(f"{fonte}/atualizacao/arquivo/{caminho}")
            except Exception as e:
                return f"falha ao baixar {caminho} de {fonte}: {type(e).__name__}; nada trocado"
            if a.status_code != 200 or _sha(a.content) != sha:
                return f"arquivo {caminho} veio errado de {fonte}; nada trocado"
            novos[destino] = a.content
        for destino, dados in novos.items():   # só troca depois de tudo baixado e conferido
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            with open(destino + ".tmp", "wb") as f:
                f.write(dados)
            os.replace(destino + ".tmp", destino)
        with open(os.path.join(base_dir, "version.txt"), "w", encoding="utf-8") as f:
            f.write(man["versao"])
        return f"atualizado de {local} para {man['versao']} ({len(novos)} arquivo(s)) por {fonte}; vale ao reabrir"
    return "nenhum servidor de atualização respondeu"


def iniciar(base_dir: str):
    def _rodar():
        try:
            print(f"[atualizador] {atualizar(base_dir)}")
        except Exception as e:
            print(f"[atualizador] aviso: {type(e).__name__}: {e}")
    threading.Thread(target=_rodar, daemon=True).start()
