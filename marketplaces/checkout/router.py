"""
marketplaces/checkout/router.py — Endpoints /checkout/* (somente leitura; não escreve no ML nem no Bling)
MZ App

GET  /checkout           página de bipagem (HTML)
POST /checkout/buscar    {codigo}: texto do QR -> envio no ML -> pedido no Bling (e registra a leitura)
GET  /checkout/sombra    modo sombra: previsão do app x situação de hoje no Bling
"""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from marketplaces.checkout import busca, registro

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
    r = busca.buscar(corpo.codigo)
    r["acao_prevista"] = registro.acao_prevista(r)   # só informativo: este módulo NÃO grava no Bling
    registro.gravar(r, corpo.codigo)
    return r


@router.get("/sombra")
def sombra():
    """Modo sombra: cruza o que o app previu com a situação de hoje no Bling (somente leitura)."""
    from marketplaces.expedicao import bling_checkout
    cache = {}

    def situacao_atual(bling_conta, numero_loja):
        try:
            if bling_conta not in cache:
                cache[bling_conta] = bling_checkout._pedidos(bling_conta)
            p = cache[bling_conta].get(str(numero_loja))
            return p["situacao"]["id"] if p else None
        except Exception:
            return None

    return registro.avaliar(registro.ler(), situacao_atual)
