"""
marketplaces/acesso_rede.py — Trava de acesso pela rede interna (PC servidor)
MZ App

O servidor pode escutar na rede (0.0.0.0) para as TVs e os PCs do galpão. Quem vem de OUTRO computador enxerga sem login
só as telas de TV; a gestão (Expedição, Full, bipagem) e o app completo abrem depois do login (cookie de sessão criado em
/auth/login). Credenciais e tokens dos marketplaces (SEMPRE_LOCAL) ficam restritos ao próprio PC (127.0.0.1), mesmo com
login. O resto recebe 401/403.
"""

import secrets
import threading
import time

from urllib.parse import quote

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

LOCAIS = {"127.0.0.1", "::1", "localhost", "testclient"}
# sem login, de outro PC: só as telas de TV e o que elas carregam
PERMITIDOS = ("/expedicao/tv", "/expedicao/painel", "/expedicao/dia", "/montagem/tv", "/montagem/tv-full", "/tv", "/static",
              "/sistema/versao", "/favicon")
# abrir uma destas telas sem login leva ao login e, depois dele, de volta à tela
GESTAO = ("/expedicao", "/montagem")
# sem login, de outro PC: a tela de login e o "esqueci a senha"
PUBLICOS = ("/", "/galpao", "/auth/login", "/auth/recuperar-senha", "/auth/redefinir-senha", "/sistema/houve-atualizacao")
# nem com login, de outro PC: credenciais/tokens dos marketplaces e abrir programa na tela do servidor
SEMPRE_LOCAL = ("/config", "/auth/url", "/auth/autorizar", "/auth/refresh", "/sistema/abrir-link", "/docs", "/openapi.json")

COOKIE = "mz_sessao"
VALIDADE_SEG = 12 * 3600
_sessoes = {}  # token -> (login, expira_em); em memória: reiniciar o servidor pede login de novo
_trava = threading.Lock()


def criar_sessao(login: str) -> str:
    token = secrets.token_urlsafe(32)
    agora = time.time()
    with _trava:
        for t in [t for t, (_, exp) in _sessoes.items() if exp < agora]:
            del _sessoes[t]
        _sessoes[token] = (login, agora + VALIDADE_SEG)
    return token


def sessao_valida(token) -> bool:
    if not token:
        return False
    with _trava:
        s = _sessoes.get(token)
    return bool(s) and s[1] > time.time()


def _casa(path: str, prefixos) -> bool:
    return any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixos)


def caminho_liberado(path: str) -> bool:
    if ".." in path:
        return False
    for p in PERMITIDOS:
        if p == "/favicon":
            if path.startswith(p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


MENU = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MZ App — Telas do galpão</title>
<style>body{font-family:Segoe UI,Arial,sans-serif;background:#f4f4f4;margin:0;padding:32px;color:#222}
h1{font-size:22px}a{display:block;background:#fff;border:1px solid #ccc;border-radius:8px;padding:16px 20px;margin:12px 0;
text-decoration:none;color:#166534;font-size:18px;font-weight:600}a small{display:block;color:#555;font-weight:400;font-size:14px}</style></head>
<body><h1>MZ App — telas do galpão</h1>
<a href="/expedicao/tv">Monitor da TV<small>Expedição do dia: cortes, quantidades e contagem regressiva</small></a>
<a href="/expedicao">Expedição (galpão)<small>Abre com login. Pedidos do dia, prontos para o envio e cancelamentos</small></a>
<a href="/montagem">Full — montagem de envios<small>Abre com login. Fila do estoquista, PDF de montagem, envios da Amazon</small></a>
<a href="/" style="color:#333">App completo<small>Abre com login (usuário e senha do MZ App)</small></a></body></html>"""


def instalar(app):
    @app.middleware("http")
    async def _acesso_rede(request, call_next):
        host = request.client.host if request.client else ""
        path = request.url.path
        if host in LOCAIS or caminho_liberado(path):
            return await call_next(request)
        if ".." in path or _casa(path, SEMPRE_LOCAL):
            return JSONResponse({"detail": "acesso restrito ao computador do servidor"}, status_code=403)
        if path == "/galpao":
            return HTMLResponse(MENU)
        if path in PUBLICOS or sessao_valida(request.cookies.get(COOKIE)):
            return await call_next(request)
        if request.method == "GET" and _casa(path, GESTAO) and "text/html" in request.headers.get("accept", ""):
            volta = path + ("?" + request.url.query if request.url.query else "")
            return RedirectResponse("/?volta=" + quote(volta, safe=""), status_code=303)
        return JSONResponse({"detail": "faça login no MZ App"}, status_code=401)
