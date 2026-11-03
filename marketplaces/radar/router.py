"""
marketplaces/radar/router.py — Endpoints /radar/* (posição dos anúncios na busca do Mercado Livre)
MZ App

GET    /radar                         página de detalhes (HTML)
GET    /radar/estado?conta=           lista de acompanhamento + resultados (com histórico) + estado do robô
GET    /radar/produto/{conta}/{mlb}   resumo para o modal: termo principal (página e posição) e demais termos
POST   /radar/watch                   cria/altera um termo de um anúncio
DELETE /radar/watch                   tira o termo da lista (o histórico fica)
POST   /radar/executar                "atualizar agora": entra na fila; o robô (outra máquina) atende

Este servidor só lê e grava dados e fila. Quem abre o navegador e pesquisa é o robô (robo.py), numa máquina só.
Hoje o backend não aplica o role do usuário a nenhum endpoint; vale também para estas rotas.
"""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from marketplaces.radar import parse, robo, store
from marketplaces.radar.store import ErroRadar

router = APIRouter(prefix="/radar", tags=["radar"])
PAGINA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pagina.html")


class Termo(BaseModel):
    conta: str
    mlb: str
    keyword: str
    titulo: Optional[str] = None
    sku: Optional[str] = None
    ativo: Optional[bool] = None
    principal: Optional[bool] = None
    autor: str = ""


class Pedido(BaseModel):
    escopo: str = "todas"          # "todas" ou uma conta (meli01...)
    mlb: Optional[str] = None
    keyword: Optional[str] = None
    autor: str = ""


@router.get("", response_class=HTMLResponse)
def pagina():
    with open(PAGINA, encoding="utf-8") as f:
        return f.read()


@router.get("/estado")
def estado(conta: str = None):
    ex = store.status().get("execucao") or {}
    return {"watch": store.listar_watch(conta), "resultados": store.listar_resultados(conta),
            "estado": store.estado(), "robo_nesta_maquina": bool(robo.config().get("robo_ativo")),
            "execucao": {k: ex.get(k) for k in ("iniciou_em", "terminou_em", "resumo", "erro", "mudancas")}}


@router.get("/produto/{conta}/{mlb}")
def produto(conta: str, mlb: str):
    if not parse.normalizar_mlb(mlb):
        raise HTTPException(status_code=400, detail="MLB inválido")
    return store.resumo_produto(conta, mlb)


@router.post("/watch")
def salvar_termo(t: Termo):
    try:
        return store.salvar_watch(t.conta, t.mlb, t.keyword, t.titulo, t.sku, t.ativo, t.principal, t.autor[:40])
    except ErroRadar as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/watch")
def remover_termo(conta: str, mlb: str, keyword: str, autor: str = ""):
    if not store.remover_watch(conta, mlb, keyword, autor[:40]):
        raise HTTPException(status_code=404, detail="termo não encontrado")
    return {"success": True}


@router.post("/executar")
def executar(p: Pedido):
    try:
        return {"success": True, "pedido": store.pedir_verificacao(p.escopo, p.mlb, p.keyword, p.autor[:40])}
    except ErroRadar as e:
        raise HTTPException(status_code=409, detail=str(e))
