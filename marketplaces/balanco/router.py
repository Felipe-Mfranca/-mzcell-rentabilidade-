"""
marketplaces/balanco/router.py — Endpoints /balanco/* (somente leitura)

GET  /balanco             página de contagem e impressão
GET  /balanco/dados       linhas de SKU físico (recorrentes e sazonais) e estado da leitura do catálogo
POST /balanco/atualizar   relê categorias e composições dos três Blings em segundo plano
GET  /balanco/geral       Relatório de Balanço Geral por Bling (página de impressão)
GET  /balanco/geral/dados linhas do relatório de um Bling
"""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from marketplaces.balanco import catalogo, geral, lista

router = APIRouter(prefix="/balanco", tags=["balanco"])
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")
PAGINA_GERAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "geral.html")


@router.get("", response_class=HTMLResponse)
def pagina():
    with open(PAGINA, encoding="utf-8") as f:
        return f.read()


@router.get("/dados")
def dados():
    estado = catalogo.atualizar()   # lê em segundo plano se o arquivo não existe ou passou de 24 h
    return dict(lista.montar(), estado=estado)


@router.get("/geral", response_class=HTMLResponse)
def pagina_geral():
    with open(PAGINA_GERAL, encoding="utf-8") as f:
        return f.read()


@router.get("/geral/dados")
def dados_geral(bling: str = "bling01"):
    try:
        return geral.montar(bling)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/atualizar")
def atualizar():
    return catalogo.atualizar(forcar=True)
