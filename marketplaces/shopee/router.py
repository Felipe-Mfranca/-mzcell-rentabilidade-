"""
marketplaces/shopee/router.py — Endpoints /shopee/* (somente leitura na Shopee)
MZCell Rentabilidade

Handlers `def` (cliente httpx síncrono; o FastAPI os roda em thread). Nenhuma rota devolve partner key,
token ou code: o `code` entra só no POST de autorização e a resposta diz apenas se deu certo.
Hoje o backend não aplica o role do usuário a nenhum endpoint; vale também para estas rotas.
"""

import html

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from marketplaces.shopee import api, lojas
from marketplaces.shopee.lojas import ErroShopee

router = APIRouter(prefix="/shopee", tags=["shopee"])


class CodigoAutorizacao(BaseModel):
    code: str
    shop_id: int


def _http(e: ErroShopee):
    return HTTPException(status_code=e.status, detail=str(e))


@router.get("/lojas")
def listar_lojas():
    try:
        env = lojas.ler_env()
        todas = lojas.lojas(env)
        return {"ambiente": lojas.ambiente(env).lower(), "app_configurado": lojas.configurado(env),
                "lojas": [{"loja": k, "nome": v["nome"], "shop_id": v["shop_id"],
                           **api.estado_autorizacao(v["shop_id"], env)} for k, v in todas.items()]}
    except ErroShopee as e:
        raise _http(e)


@router.get("/autorizacao/{loja}/url")
def url_autorizacao(loja: str):
    try:
        lojas.shop_id_da(loja)
        return {"loja": loja, "url": api.url_autorizacao()}
    except ErroShopee as e:
        raise _http(e)


@router.post("/autorizacao/{loja}/codigo")
def receber_codigo(loja: str, corpo: CodigoAutorizacao):
    try:
        esperado = lojas.shop_id_da(loja)
        if corpo.shop_id != esperado:
            raise ErroShopee(f"o shop_id informado não é o da loja '{loja}'", 400)
        api.trocar_codigo(corpo.code, corpo.shop_id)
        return {"loja": loja, "autorizada": True, **api.estado_autorizacao(corpo.shop_id)}
    except ErroShopee as e:
        raise _http(e)


@router.get("/retorno", response_class=HTMLResponse)
def retorno_autorizacao(code: str, shop_id: int):
    """Destino do redirect da Shopee (SHOPEE_REDIRECT_URL = http://127.0.0.1:8001/shopee/retorno)."""
    try:
        if shop_id not in {v["shop_id"] for v in lojas.lojas().values()}:
            raise ErroShopee("esse shop_id não é de nenhuma loja cadastrada em shopee_lojas.json", 400)
        api.trocar_codigo(code, shop_id)
    except ErroShopee as e:
        return HTMLResponse(f"<h3>Autorização não concluída</h3><p>{html.escape(str(e))}</p>", status_code=e.status)
    return HTMLResponse("<h3>Loja autorizada.</h3><p>Pode fechar esta janela e voltar ao MZ Rentabilidade.</p>")


@router.get("/teste/{loja}")
def testar_loja(loja: str):
    try:
        return {"loja": loja, **api.info_loja(lojas.shop_id_da(loja))}
    except ErroShopee as e:
        raise _http(e)


@router.get("/alertas")
def alertas():
    """Lojas com autorização vencida ou perto de vencer (para a faixa de aviso da tela)."""
    try:
        env = lojas.ler_env()
        saida = []
        for k, v in lojas.lojas(env).items():
            est = api.estado_autorizacao(v["shop_id"], env)
            if est["nivel"] != "ok":
                saida.append({"loja": k, "nome": v["nome"], **est})
        return {"alertas": saida}
    except ErroShopee as e:
        raise _http(e)
