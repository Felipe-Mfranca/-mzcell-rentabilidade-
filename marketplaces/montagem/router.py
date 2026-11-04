"""
marketplaces/montagem/router.py — Endpoints /montagem/* (controle interno; não escreve em nenhuma API)

GET    /montagem                           gestão (líder e equipe)
GET    /montagem/tv                        painel para TV (tela cheia, atualiza sozinho)
GET    /montagem/dados                     gestão: envios, progresso e quem marcou
GET    /montagem/tv/dados                  TV: só andamento e responsáveis
GET    /montagem/produtividade             gestão: marcas e tempos por pessoa
POST   /montagem/pdf                       lê o PDF do Full do Meli e devolve a prévia (não grava)
                                           (Amazon e Shopee entram à mão: canal + número + produtos)
POST   /montagem/envios                    cria o envio com conta, coleta, prazo e responsáveis
POST   /montagem/envios/{frete}/produtos/{i}/marcar     {tag: T|E|OK, por, papel: estoquista|lider}
DELETE /montagem/envios/{frete}/produtos/{i}/marcar/{tag}
POST   /montagem/envios/{frete}/produtos/{i}/responsaveis   {estoquista, embalador}
POST   /montagem/envios/{frete}/prazo      {prazo, coleta}
DELETE /montagem/envios/{frete}
"""

import json
import os

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from marketplaces.montagem import armazenamento as arm
from marketplaces.montagem import leitor

router = APIRouter(prefix="/montagem", tags=["montagem"])
PASTA = os.path.dirname(os.path.abspath(__file__))
PRODUTOS = os.path.join(arm.BASE_DIR, "produtos.json")


def _pagina(nome):
    with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
        return HTMLResponse(f.read())


def _erro(e: arm.ErroMontagem):
    return JSONResponse({"success": False, "error": str(e)}, status_code=e.status)


@router.get("", response_class=HTMLResponse)
def gestao():
    return _pagina("pagina.html")


@router.get("/tv", response_class=HTMLResponse)
def tv():
    return _pagina("tv.html")


@router.get("/dados")
def dados():
    """Gestão: tudo, inclusive quem marcou cada etapa e quando."""
    envios = arm.listar()
    return {"agora": arm.agora(), "envios": [dict(e, resumo=arm.resumo(e)) for e in envios]}


@router.get("/tv/dados")
def dados_tv():
    """TV: andamento e responsáveis, sem produtividade."""
    return {"agora": arm.agora(), "envios": [arm.para_tv(e) for e in arm.listar() if not e.get("concluido_em")]}


@router.get("/produtividade")
def produtividade():
    return arm.produtividade()


def _contas_do_sku() -> dict:
    """sku (minúsculo) -> contas Meli que anunciam esse SKU. Só leitura local."""
    try:
        with open(PRODUTOS, encoding="utf-8") as f:
            prods = json.load(f)
    except Exception:
        return {}
    mapa = {}
    for x in prods.values():
        if isinstance(x, dict) and x.get("sku"):
            mapa.setdefault(str(x["sku"]).lower(), set()).add(x.get("conta"))
    return mapa


@router.post("/pdf")
async def previa(file: UploadFile = File(...)):
    try:
        res = leitor.ler_pdf(await file.read())
    except Exception as e:
        return JSONResponse({"success": False, "error": f"Não consegui ler o PDF: {str(e)[:200]}"}, status_code=400)
    mapa = _contas_do_sku()
    contas = set()
    for p in res["produtos"]:
        p["contas"] = sorted(c for c in mapa.get(p["sku"].lower(), set()) if c)
        contas.update(p["contas"])
    res["conta_sugerida"] = sorted(contas)[0] if len(contas) == 1 else ""
    res["ja_cadastrado"] = f"meli-{res['frete']}" in {e["chave"] for e in arm.listar()}
    return {"success": True, **res}


class NovoEnvio(BaseModel):
    frete: str            # número do envio no canal (Frete # no Meli, ID FBA na Amazon, inbound na Shopee)
    canal: str = "meli"   # meli | amazon | shopee
    conta: str = ""
    coleta: str = ""
    prazo: str
    produtos: list
    criado_por: str = ""


@router.post("/envios")
def criar(b: NovoEnvio):
    try:
        return {"success": True, "envio": arm.criar(b.frete, b.conta, b.coleta, b.prazo, b.produtos, b.criado_por, b.canal)}
    except arm.ErroMontagem as e:
        return _erro(e)


class Marca(BaseModel):
    tag: str
    por: str
    papel: str = "estoquista"   # estoquista (do produto) ou lider


@router.post("/envios/{frete}/produtos/{i}/marcar")
def marcar(frete: str, i: int, b: Marca):
    try:
        return {"success": True, "envio": arm.marcar(frete, i, b.tag, b.por, b.papel)}
    except arm.ErroMontagem as e:
        return _erro(e)


@router.delete("/envios/{frete}/produtos/{i}/marcar/{tag}")
def desmarcar(frete: str, i: int, tag: str):
    try:
        return {"success": True, "envio": arm.desmarcar(frete, i, tag)}
    except arm.ErroMontagem as e:
        return _erro(e)


class Responsaveis(BaseModel):
    estoquista: str = None
    embalador: str = None


@router.post("/envios/{frete}/produtos/{i}/responsaveis")
def responsaveis(frete: str, i: int, b: Responsaveis):
    try:
        return {"success": True, "envio": arm.atribuir(frete, i, b.estoquista, b.embalador)}
    except arm.ErroMontagem as e:
        return _erro(e)


class Prazo(BaseModel):
    prazo: str
    coleta: str = None


@router.post("/envios/{frete}/prazo")
def prazo(frete: str, b: Prazo):
    try:
        return {"success": True, "envio": arm.alterar_prazo(frete, b.prazo, b.coleta)}
    except arm.ErroMontagem as e:
        return _erro(e)


@router.delete("/envios/{frete}")
def remover(frete: str):
    try:
        arm.remover(frete)
        return {"success": True}
    except arm.ErroMontagem as e:
        return _erro(e)
