"""
marketplaces/acesso_rede.py — Trava de acesso pela rede interna (PC servidor)
MZ App

O servidor pode escutar na rede (0.0.0.0) para as TVs e os PCs do galpão. Quem vem de OUTRO computador só enxerga as
telas operacionais; tudo que mexe com credenciais, usuários, custos ou escreve em marketplace fica restrito ao próprio PC
(127.0.0.1). Quem acessa de fora de 127.0.0.1 e tenta qualquer outro caminho recebe 403.
"""

from starlette.responses import HTMLResponse, JSONResponse

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


MENU = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MZ App — Telas do galpão</title>
<style>body{font-family:Segoe UI,Arial,sans-serif;background:#f4f4f4;margin:0;padding:32px;color:#222}
h1{font-size:22px}a{display:block;background:#fff;border:1px solid #ccc;border-radius:8px;padding:16px 20px;margin:12px 0;
text-decoration:none;color:#166534;font-size:18px;font-weight:600}a small{display:block;color:#555;font-weight:400;font-size:14px}</style></head>
<body><h1>MZ App — telas do galpão</h1>
<a href="/expedicao/tv">Monitor da TV<small>Expedição do dia: cortes, quantidades e contagem regressiva</small></a>
<a href="/expedicao">Expedição (galpão)<small>Pedidos do dia, prontos para o envio e cancelamentos</small></a>
<a href="/montagem">Full — montagem de envios<small>Fila do estoquista, PDF de montagem, envios da Amazon</small></a>
<p style="color:#666;font-size:13px">O app completo (login, custos, SAC, financeiro) abre só no computador do servidor.</p></body></html>"""


def instalar(app):
    @app.middleware("http")
    async def _acesso_rede(request, call_next):
        host = request.client.host if request.client else ""
        if host in LOCAIS or caminho_liberado(request.url.path):
            return await call_next(request)
        if request.url.path == "/":
            return HTMLResponse(MENU)
        return JSONResponse({"detail": "acesso restrito ao computador do servidor"}, status_code=403)
