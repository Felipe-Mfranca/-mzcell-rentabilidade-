"""
marketplaces/montagem/router.py — Endpoints /montagem/* (controle interno; não escreve em nenhuma API)

GET    /montagem                           gestão (líder e equipe)
GET    /montagem/tv                        painel para TV (tela cheia, atualiza sozinho)
GET    /montagem/dados                     gestão: envios, progresso e quem marcou
GET    /montagem/tv/dados                  TV: só andamento e responsáveis
GET    /montagem/produtividade?dia=        gestão: marcas e tempos por pessoa e duração de cada Full (log do dia)
POST   /montagem/pdf                       lê o PDF do Full do Meli e devolve a prévia (não grava)
                                           (Amazon e Shopee entram à mão: canal + número + produtos)
POST   /montagem/envios                    cria o envio com conta, coleta, prazo e responsáveis
POST   /montagem/envios/{frete}/produtos/{i}/marcar     {tag: T|E|OK, por, papel: estoquista|lider}
DELETE /montagem/envios/{frete}/produtos/{i}/marcar/{tag}
POST   /montagem/envios/{frete}/produtos/{i}/responsaveis   {estoquista, embalador}
POST   /montagem/envios/{frete}/prazo      {prazo, coleta}
POST   /montagem/envios/{frete}/iniciar    {por, papel: lider} — tira da fila
POST   /montagem/envios/{frete}/ordem      {posicao} — 1 = próximo da fila
DELETE /montagem/envios/{frete}

Todo envio novo entra na fila. Os Fulls da Amazon prontos (NF + coleta) entram sozinhos a cada 20 min.
"""

import json
import os
import threading
import time

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from marketplaces.montagem import armazenamento as arm
from marketplaces.montagem import amazon_fba, leitor

router = APIRouter(prefix="/montagem", tags=["montagem"])
PASTA = os.path.dirname(os.path.abspath(__file__))
PRODUTOS = os.path.join(arm.BASE_DIR, "produtos.json")


AUTO_AMAZON_SEG = 20 * 60


def _loop_amazon():
    time.sleep(60)                     # deixa o servidor subir antes de consultar a Amazon
    while True:
        try:
            criados = amazon_fba.importar_prontos()
            if criados:
                print(f"[montagem] Fulls da Amazon na fila: {', '.join(criados)}")
        except Exception as e:
            print(f"[montagem] Pré-carga automática da Amazon falhou: {str(e)[:150]}")
        time.sleep(AUTO_AMAZON_SEG)


@router.on_event("startup")
def _ligar_auto_amazon():
    threading.Thread(target=_loop_amazon, daemon=True, name="montagem-amazon").start()


def _pagina(nome):
    with open(os.path.join(PASTA, nome), encoding="utf-8") as f:
        return HTMLResponse(f.read())


def _erro(e: arm.ErroMontagem):
    return JSONResponse({"success": False, "error": str(e)}, status_code=e.status)


@router.get("", response_class=HTMLResponse)
def gestao():
    return _pagina("pagina.html")


@router.get("/tv")
def tv():
    """A TV é o painel completo da Expedição (Flex, Coleta, Places, DBA, Shopee, cortes e Full)."""
    return RedirectResponse("/expedicao/painel?tv=1&real=1")


@router.get("/tv-full", response_class=HTMLResponse)
def tv_full():
    """Versão antiga, só com as montagens do Full."""
    return _pagina("tv.html")


@router.get("/dados")
def dados():
    """Gestão: tudo, inclusive quem marcou cada etapa e quando."""
    envios = arm.listar()
    return {"agora": arm.agora(), "envios": [dict(e, resumo=arm.resumo(e), status=arm.status(e), falta=arm.falta_programar(e))
                                             for e in envios]}


@router.get("/tv/dados")
def dados_tv():
    """TV: andamento e responsáveis, sem produtividade."""
    return {"agora": arm.agora(), "envios": [arm.para_tv(e) for e in arm.listar() if not e.get("concluido_em")]}


@router.get("/produtividade")
def produtividade(dia: str = None):
    return arm.produtividade(dia)


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
    res.setdefault("canal", "meli")
    if res["canal"] == "shopee":
        res["conta_sugerida"] = "shopee"
    res["ja_cadastrado"] = f"{res['canal']}-{res['frete']}" in {e["chave"] for e in arm.listar()}
    return {"success": True, **res}


@router.get("/amazon/disponiveis")
def amazon_disponiveis(atualizar: bool = False):
    """Envios FBA da Amazon (somente leitura): prontos para montar (NF emitida + coleta agendada) e os que ainda faltam algo."""
    try:
        dados = amazon_fba.disponiveis(atualizar)
    except Exception as e:
        return JSONResponse({"success": False, "error": f"Não consegui consultar a Amazon: {str(e)[:200]}"}, status_code=502)
    cadastrados = {e["chave"] for e in arm.listar()} | arm.dispensados()
    envios = [dict(x, ja_cadastrado=f"amazon-{x.get('numero')}" in cadastrados) for x in dados["envios"]]
    return {"success": True, "envios": envios, "erros": dados["erros"], "gerado_em": dados["gerado_em"]}


class ImportarAmazon(BaseModel):
    numeros: list         # IDs FBA (shipmentConfirmationId) a importar
    criado_por: str = ""


@router.post("/amazon/importar")
def amazon_importar(b: ImportarAmazon):
    """Cria na fila os envios FBA escolhidos, sem responsáveis (o estoquista atribui e começa)."""
    try:
        dados = amazon_fba.disponiveis(False)
    except Exception as e:
        return JSONResponse({"success": False, "error": f"Não consegui consultar a Amazon: {str(e)[:200]}"}, status_code=502)
    por_numero = {x.get("numero"): x for x in dados["envios"] if x.get("pronto")}
    criados, falhas = [], []
    for n in b.numeros:
        x = por_numero.get(n)
        if not x:
            falhas.append({"numero": n, "motivo": "não está pronto (NF emitida e coleta agendada) ou não foi encontrado"})
            continue
        try:
            arm.criar(n, x["conta"], x["coleta"], "", x["produtos"], b.criado_por, "amazon")
            criados.append(n)
        except arm.ErroMontagem as e:
            falhas.append({"numero": n, "motivo": str(e)})
    return {"success": True, "criados": criados, "falhas": falhas}


class NovoEnvio(BaseModel):
    frete: str            # número do envio no canal (Frete # no Meli, ID FBA na Amazon, inbound na Shopee)
    canal: str = "meli"   # meli | amazon | shopee
    conta: str = ""
    coleta: str = ""
    prazo: str = ""       # pode ficar pendente enquanto está na fila
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
    prazo: str = ""
    coleta: str = None


@router.post("/envios/{frete}/prazo")
def prazo(frete: str, b: Prazo):
    try:
        return {"success": True, "envio": arm.alterar_prazo(frete, b.prazo, b.coleta)}
    except arm.ErroMontagem as e:
        return _erro(e)


class Inicio(BaseModel):
    por: str
    papel: str


@router.post("/envios/{frete}/iniciar")
def iniciar(frete: str, b: Inicio):
    try:
        return {"success": True, "envio": arm.iniciar(frete, b.por, b.papel)}
    except arm.ErroMontagem as e:
        return _erro(e)


class Ordem(BaseModel):
    posicao: int


@router.post("/envios/{frete}/ordem")
def ordem(frete: str, b: Ordem):
    try:
        return {"success": True, "envio": arm.mover(frete, b.posicao)}
    except arm.ErroMontagem as e:
        return _erro(e)


@router.delete("/envios/{frete}")
def remover(frete: str):
    try:
        arm.remover(frete)
        return {"success": True}
    except arm.ErroMontagem as e:
        return _erro(e)
