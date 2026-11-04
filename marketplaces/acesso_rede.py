"""
marketplaces/acesso_rede.py — Trava de acesso pela rede interna (PC servidor)
MZ App

O servidor pode escutar na rede (0.0.0.0) para as TVs e os PCs do galpão. Quem vem de OUTRO computador só enxerga as
telas operacionais; tudo que mexe com credenciais, usuários, custos ou escreve em marketplace fica restrito ao próprio PC
(127.0.0.1). Quem acessa de fora de 127.0.0.1 e tenta qualquer outro caminho recebe 403.
"""

from starlette.responses import JSONResponse

LOCAIS = {"127.0.0.1", "::1", "localhost", "testclient"}
PERMITIDOS = ("/expedicao", "/montagem", "/tv", "/static", "/fotos/", "/sistema/versao", "/bling/estoque", "/favicon")


def caminho_liberado(path: str) -> bool:
    if ".." in path:
        return False
    for p in PERMITIDOS:
        if p == "/favicon":
            if path.startswith(p):
                return True
        elif p.endswith("/"):
            if path.startswith(p):
                return True
        elif path == p or path.startswith(p + "/"):
            return True
    return False


def instalar(app):
    @app.middleware("http")
    async def _acesso_rede(request, call_next):
        host = request.client.host if request.client else ""
        if host in LOCAIS or caminho_liberado(request.url.path):
            return await call_next(request)
        return JSONResponse({"detail": "acesso restrito ao computador do servidor"}, status_code=403)
