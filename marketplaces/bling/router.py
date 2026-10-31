"""
marketplaces/bling/router.py — GET /bling/estoque/{conta} (somente leitura)
"""

import threading

from fastapi import APIRouter, HTTPException

from marketplaces.bling import estoque, skus_filho

router = APIRouter(prefix="/bling", tags=["bling"])
threading.Timer(20, estoque.aquecer).start()  # depois que o servidor terminou de subir


@router.get("/estoque/{conta}")
def estoque_conta(conta: str, atualizar: bool = False):
    """Estoque por SKU no Bling da empresa da conta e, para o tooltip, nos outros dois."""
    if conta not in estoque.BLING_DA_CONTA:
        raise HTTPException(status_code=400, detail="conta inválida")
    res = estoque.por_conta(conta, forcar=atualizar)
    res["filhos"] = skus_filho.por_conta(conta, forcar=atualizar) if conta.startswith("meli") else {}
    return res
