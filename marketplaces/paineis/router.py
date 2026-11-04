"""
marketplaces/paineis/router.py — Painéis de TV por área e painel de gestão geral (diretoria)
MZ App

GET /tv                lista dos painéis
GET /tv/{area}         painel da área em tela cheia (atualiza sozinho)
GET /tv/{area}/dados   dados do painel (somente leitura)

Áreas em construção mostram "em desenvolvimento". Expedição já aponta para o painel real.
Módulo fechado por padrão (modulos_liberados.py → "paineis"); abre por máquina em modulos_locais.json.
"""

import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(prefix="/tv", tags=["paineis"])
PASTA = os.path.dirname(os.path.abspath(__file__))

# area -> (título, público, estado). "pronto" = painel real; "em_desenvolvimento" = só a moldura.
AREAS = {
    "expedicao": ("Expedição", "galpão", "pronto"),
    "sac": ("SAC — pendências e contestações", "atendimento", "em_desenvolvimento"),
    "financeiro": ("Financeiro", "financeiro", "em_desenvolvimento"),
    "compras": ("Compras e reposição", "compras", "em_desenvolvimento"),
    "vendas": ("Vendas e marketplaces", "comercial", "em_desenvolvimento"),
    "geral": ("Gestão geral", "diretoria", "em_desenvolvimento"),
}


def _html(nome: str) -> str:
    with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
        return f.read()


@router.get("", response_class=HTMLResponse)
def indice():
    itens = "".join(
        f'<li><a href="/tv/{k}">{t}</a> <small>({pub}{"" if est == "pronto" else ", em desenvolvimento"})</small></li>'
        for k, (t, pub, est) in AREAS.items())
    return HTMLResponse(f"<meta charset='utf-8'><title>Painéis de TV</title><body style='font:20px system-ui;margin:40px'>"
                        f"<h1>Painéis de TV</h1><ul>{itens}</ul></body>")


@router.get("/{area}")
def painel(area: str):
    if area not in AREAS:
        raise HTTPException(status_code=404, detail="painel inexistente")
    if area == "expedicao":
        return RedirectResponse("/expedicao/tv")
    titulo, publico, _ = AREAS[area]
    return HTMLResponse(_html("tv.html").replace("{{AREA}}", area).replace("{{TITULO}}", titulo).replace("{{PUBLICO}}", publico))


@router.get("/{area}/dados")
def dados(area: str):
    if area not in AREAS:
        raise HTTPException(status_code=404, detail="painel inexistente")
    titulo, publico, estado = AREAS[area]
    return {"area": area, "titulo": titulo, "publico": publico, "estado": estado,
            "agora": datetime.now().isoformat(timespec="seconds"), "blocos": []}
