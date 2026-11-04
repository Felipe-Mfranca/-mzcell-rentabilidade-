"""
marketplaces/checkout/router.py — Endpoints /checkout/* (somente leitura; não escreve no ML nem no Bling)
MZ App

GET  /checkout           página de bipagem (HTML)
POST /checkout/buscar    {codigo}: texto do QR -> envio no ML -> pedido no Bling
"""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from marketplaces.checkout import busca

router = APIRouter(prefix="/checkout", tags=["checkout"])
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")


class CodigoIn(BaseModel):
    codigo: str


@router.get("", response_class=HTMLResponse)
def pagina():
    with open(PAGINA, encoding="utf-8") as f:
        return f.read()


@router.post("/buscar")
def buscar(corpo: CodigoIn):
    return busca.buscar(corpo.codigo)
