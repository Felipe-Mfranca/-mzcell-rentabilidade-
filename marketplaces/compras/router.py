"""
marketplaces/compras/router.py — Endpoints /compras/* (somente leitura)

GET  /compras             página
GET  /compras/dados       envios ao Full, pedido de compra e empréstimos entre Blings (cache de 6 h)
POST /compras/atualizar   recalcula em segundo plano (alguns minutos)
"""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from marketplaces.compras import previsao

router = APIRouter(prefix="/compras", tags=["compras"])
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")


@router.get("", response_class=HTMLResponse)
def pagina():
    with open(PAGINA, encoding="utf-8") as f:
        return f.read()


@router.get("/dados")
def dados():
    return previsao.dados()


@router.post("/atualizar")
def atualizar():
    return previsao.dados(forcar=True)
