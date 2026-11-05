"""
marketplaces/expedicao/router.py — Endpoints /expedicao/* (somente leitura)
MZ App

GET /expedicao          página do mapa (HTML)
GET /expedicao/galpao   JSON com os envios do galpão e os prazos (ML; Amazon e Shopee entram depois)
"""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from pydantic import BaseModel

from datetime import date, datetime, timedelta

from marketplaces.expedicao import amazon_dba, bipagem, bling_checkout, cancelamentos as cancelamentos_mod, cortes as cortes_mod, dia as dia_mod, marcas, ml, simulacao, snapshot

router = APIRouter(prefix="/expedicao", tags=["expedicao"])
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")


class MarcaIn(BaseModel):
    envio: str
    campo: str
    valor: bool


@router.get("", response_class=HTMLResponse)
def pagina():
    with open(PAGINA, encoding="utf-8") as f:
        return f.read()


@router.get("/full")
def full_gestao():
    """Gestão das montagens do Full (Meli, Amazon, Shopee): parte do módulo Expedição."""
    return RedirectResponse("/montagem")


@router.get("/tv")
def monitor_tv():
    """Monitor da TV: painel do dia da Expedição com dados reais, em tela cheia."""
    return RedirectResponse("/expedicao/painel?tv=1&real=1")


@router.get("/galpao")
def galpao(contas: str = None, atualizar: bool = False):
    lista = [c for c in (contas or "").split(",") if c] or None
    if lista and any(c not in ml.CONTAS for c in lista):
        raise HTTPException(status_code=400, detail="conta inválida")
    res = ml.mapa(lista, forcar=atualizar)
    linhas, erros_bling = bling_checkout.enriquecer(res["linhas"])
    return dict(res, linhas=linhas, erros_bling=erros_bling)


@router.get("/bipar", response_class=HTMLResponse)
def bipar_pagina():
    """Tela de bipagem: etiqueta -> número do pedido no Bling e qual Bling (1, 2 ou 3)."""
    with open(os.path.join(os.path.dirname(PAGINA), "bipar.html"), encoding="utf-8") as f:
        return f.read()


@router.get("/bipar/buscar")
def bipar_buscar(codigo: str, conta: str = None):
    try:
        return bipagem.buscar(codigo, conta or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/cancelamentos")
def cancelamentos(dias: int = 7, atualizar: bool = False):
    """Pedidos cancelados no ML que continuam ativos no Bling (o aviso some quando o Bling passa para Cancelado)."""
    if not 1 <= dias <= 30:
        raise HTTPException(status_code=400, detail="dias deve estar entre 1 e 30")
    return cancelamentos_mod.pendentes(dias, forcar=atualizar)


@router.get("/marcas")
def listar_marcas():
    try:
        return marcas.ler()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/marca")
def marcar(body: MarcaIn):
    try:
        return marcas.marcar(body.envio, body.campo, body.valor)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Painel do dia (Flex, Coleta, Places e Full) — módulo "montagem": só abre onde modulos_locais.json permitir ──
def _painel_liberado():
    try:
        from modulos_liberados import liberado
    except ImportError:
        liberado = lambda nome: False  # noqa: E731
    if not liberado("montagem"):
        raise HTTPException(status_code=404, detail="Não encontrado")


@router.get("/painel", response_class=HTMLResponse)
def painel():
    _painel_liberado()
    with open(os.path.join(os.path.dirname(PAGINA), "painel.html"), encoding="utf-8") as f:
        return f.read()


@router.get("/dia")
def dia(simular: bool = False, dia: str = None, hora: str = None, atualizar: bool = False):
    """Painel do dia. `simular=1` usa dados fictícios (nada real é lido nem gravado); `dia` AAAA-MM-DD (padrão: hoje;
    na simulação, amanhã) e `hora` HH:MM (só na simulação: o relógio simulado)."""
    _painel_liberado()
    hoje = datetime.now(dia_mod.BRT)
    try:
        alvo = date.fromisoformat(dia) if dia else ((hoje + timedelta(days=1)).date() if simular else hoje.date())
        if simular:
            h, m = (hora or "05:00").split(":")
            agora = datetime(alvo.year, alvo.month, alvo.day, int(h), int(m), tzinfo=dia_mod.BRT)
        else:
            agora = hoje
    except ValueError:
        raise HTTPException(status_code=400, detail="dia (AAAA-MM-DD) ou hora (HH:MM) inválidos")
    if simular:
        linhas, marc = simulacao.pedidos(alvo, agora)
        full = simulacao.envios_full(alvo, agora)
    else:
        snap = snapshot.obter(forcar=atualizar)
        if snap["dados"] is None:      # primeira carga desde que o servidor subiu: a tela mostra "carregando" e tenta de novo
            return {"modo": "real", "carregando": True, "erro": snap["erro"], "dia": alvo.isoformat(),
                    "agora": agora.isoformat(timespec="seconds")}
        linhas = snap["dados"]["linhas"]
        res = {"erros": snap["dados"]["erros"]}
        try:
            marc = marcas.ler()
        except RuntimeError:
            marc = {}
        try:
            from marketplaces.montagem import armazenamento as arm
            full = arm.listar()
        except ImportError:
            full = []
    saida = dia_mod.montar(linhas, marc, full, agora, alvo, cortes_mod.carregar())
    saida["modo"] = "simulacao" if simular else "real"
    if not simular:
        saida["erros"] = res.get("erros", {})
        saida["atualizado_em"] = snap["iso"]
        saida["idade_seg"] = snap["idade"]
        saida["atualizando"] = snap["carregando"]
    return saida
