"""
marketplaces/checkout/router.py — Endpoints /checkout/* (nunca escreve no ML; só /confirmar pode escrever no Bling, simulando por padrão e travado)
MZ App

GET  /checkout           página de bipagem (HTML)
POST /checkout/buscar    {codigo}: texto do QR -> envio no ML -> pedido no Bling (e registra a leitura)
POST /checkout/confirmar {bling_conta, pedido_id, simular=true}: marca Verificado (travado; ver gravacao.py)
GET  /checkout/sombra    modo sombra: previsão do app x situação de hoje no Bling
"""

import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from marketplaces.checkout import busca, gravacao, registro

router = APIRouter(prefix="/checkout", tags=["checkout"])
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")


class CodigoIn(BaseModel):
    codigo: str


class ConfirmarIn(BaseModel):
    bling_conta: str
    pedido_id: int
    simular: bool = True     # padrão: só mostra o que enviaria


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


@router.post("/confirmar")
def confirmar(corpo: ConfirmarIn):
    """Checkout no Bling. Único endpoint que pode escrever; simula por padrão e tem travas (gravacao.py)."""
    if corpo.bling_conta not in ("bling01", "bling02", "bling03"):
        return JSONResponse({"success": False, "error": "Conta Bling inválida."}, status_code=400)
    try:
        return {"success": True, **gravacao.mudar_situacao(corpo.bling_conta, corpo.pedido_id, "Verificado", corpo.simular)}
    except gravacao.ErroGravacao as e:
        return JSONResponse({"success": False, "error": str(e)}, status_code=409)
