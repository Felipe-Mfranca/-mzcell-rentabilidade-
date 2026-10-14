"""
marketplaces/amazon/api.py — Acesso à SP-API da Amazon (somente leitura por enquanto)
MZCell Rentabilidade

Credenciais ficam em amazon_config.json (no .gitignore). O access token vive só em
memória: expira em ~1h e não há motivo para ele ir parar em disco.

Regras do projeto:
  - Vendas são apuradas por ASIN, somando todas as logísticas (FBA e DBA).
  - O inventário FBA é consolidado por ASIN antes de qualquer cálculo.
  - Dados da Amazon não entram no cálculo de margem/CMV sem passar por metrics.
"""

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from marketplaces.amazon import calculos

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(BASE_DIR, "amazon_config.json")

AUTH_URL = "https://api.amazon.com/auth/o2/token"
ENDPOINTS = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}
# Mesmas credenciais, só muda o host. O sandbox é estático e devolve dados fictícios.
ENDPOINTS_SANDBOX = {
    "na": "https://sandbox.sellingpartnerapi-na.amazon.com",
    "eu": "https://sandbox.sellingpartnerapi-eu.amazon.com",
    "fe": "https://sandbox.sellingpartnerapi-fe.amazon.com",
}
MARKETPLACE_BR = "A2Q3Y263D00KWC"

# "Dia fechado" é o dia do vendedor, não o do servidor. O Seller Central fecha o dia à
# meia-noite de Brasília; em UTC o "ontem" terminaria às 21h, deixando de fora o começo
# da noite, quando saem muitos pedidos. Offset fixo porque o Brasil não tem horário de
# verão desde 2019 (e evita depender do tzdata, que o Windows não traz).
FUSOS = {
    "America/Sao_Paulo": timezone(timedelta(hours=-3)),
    "UTC": timezone.utc,
}

_tokens = {}  # conta -> (access_token, expira_em em time.monotonic())
_lock = threading.Lock()


class ErroAmazon(Exception):
    """Falha previsível; `status` já é o código HTTP que o router deve devolver."""

    def __init__(self, mensagem: str, status: int = 502):
        super().__init__(mensagem)
        self.status = status


# ─── Credenciais nunca saem em mensagem de erro ──────────────────────────────

_SEGREDOS = [
    (re.compile(r"Atzr\|[\w\-|.~+/=]{20,}"), "Atzr|«REFRESH TOKEN OCULTO»"),
    (re.compile(r"Atza\|[\w\-|.~+/=]{20,}"), "Atza|«ACCESS TOKEN OCULTO»"),
    (re.compile(r"amzn1\.oa2-cs\.v1\.[0-9a-fA-F]+"), "amzn1.oa2-cs.v1.«SECRET OCULTO»"),
    (re.compile(r"(amzn1\.application-oa2-client\.)[0-9a-fA-F]+"), r"\1«OCULTO»"),
    (re.compile(r"\b[0-9a-fA-F]{40,}\b"), "«HASH OCULTO»"),
]


def mascarar(texto) -> str:
    """Corpo de erro da Amazon às vezes ecoa credenciais; apaga antes de exibir ou logar."""
    texto = str(texto)
    for padrao, troca in _SEGREDOS:
        texto = padrao.sub(troca, texto)
    return texto


# ─── Config ──────────────────────────────────────────────────────────────────

def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        raise ErroAmazon("amazon_config.json não encontrado — copie amazon_config.exemplo.json "
                         "para amazon_config.json e preencha as credenciais", 400)
    except ValueError as e:
        raise ErroAmazon(f"amazon_config.json inválido: {e}", 500)


def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def contas() -> list:
    return list(load_config().get("contas", {}))


def conta_cfg(conta_id: str) -> dict:
    c = load_config().get("contas", {}).get(conta_id)
    if c is None:
        raise ErroAmazon(f"Conta {conta_id} não existe em amazon_config.json", 404)
    return c


def _preenchido(valor) -> bool:
    return bool(valor) and not str(valor).startswith("COLE_AQUI")


def autenticado(conta_id: str) -> bool:
    """True se as três credenciais estão preenchidas. Não chama a Amazon."""
    try:
        c = conta_cfg(conta_id)
    except ErroAmazon:
        return False
    return all(_preenchido(c.get(k)) for k in ("refresh_token", "client_id", "client_secret"))


def base_url(conta_id: str) -> str:
    c = conta_cfg(conta_id)
    tabela = ENDPOINTS_SANDBOX if c.get("sandbox") else ENDPOINTS
    return tabela.get((c.get("region") or "na").lower(), tabela["na"])


def _motivo_lwa(r) -> str:
    """
    Extrai o que importa da recusa do LWA. O corpo traz um `error_index` enorme que,
    num corte por tamanho, empurra para fora o `error` e o `error_description`, que são
    justamente o motivo (invalid_client, invalid_grant...).
    """
    try:
        j = r.json()
        partes = [str(j[k]) for k in ("error", "error_description") if j.get(k)]
        if partes:
            return mascarar(" — ".join(partes))
    except ValueError:
        pass
    return mascarar(r.text[:300])


# ─── Token LWA ───────────────────────────────────────────────────────────────

def access_token(conta_id: str, forcar: bool = False) -> str:
    c = conta_cfg(conta_id)
    with _lock:
        cache = _tokens.get(conta_id)
        if cache and not forcar and cache[1] > time.monotonic() + 60:
            return cache[0]

    faltando = [k for k in ("refresh_token", "client_id", "client_secret") if not _preenchido(c.get(k))]
    if faltando:
        raise ErroAmazon(f"Credenciais incompletas em {conta_id}: {', '.join(faltando)}", 400)

    try:
        r = httpx.post(AUTH_URL, timeout=20,
                       headers={"Content-Type": "application/x-www-form-urlencoded"},
                       data={"grant_type": "refresh_token", "refresh_token": c["refresh_token"],
                             "client_id": c["client_id"], "client_secret": c["client_secret"]})
    except httpx.HTTPError as e:
        raise ErroAmazon(f"Não alcancei a Amazon (rede/proxy/firewall): {mascarar(e)}", 502)
    if r.status_code != 200:
        raise ErroAmazon(f"LWA recusou a credencial ({r.status_code}): {_motivo_lwa(r)}", 401)

    data = r.json()
    token = data["access_token"]
    with _lock:
        _tokens[conta_id] = (token, time.monotonic() + int(data.get("expires_in", 3600)))
    novo = data.get("refresh_token")
    if novo and novo != c["refresh_token"]:
        cfg = load_config()
        cfg["contas"][conta_id]["refresh_token"] = novo
        save_config(cfg)
    return token


# ─── Requisição ──────────────────────────────────────────────────────────────

def _enviar(conta_id: str, metodo: str, caminho: str, params: dict = None, corpo=None):
    """
    Requisição autenticada. Renova o token uma vez em 401 e recua em 429.
    Devolve (status, corpo) — o status é dado, não exceção: o diagnóstico depende dele.
    """
    url = base_url(conta_id) + caminho
    renovou, espera = False, 2
    for _ in range(5):
        try:
            r = httpx.request(metodo, url, params=params or None, json=corpo, timeout=30, headers={
                "x-amz-access-token": access_token(conta_id),
                "Content-Type": "application/json"})
        except httpx.HTTPError as e:
            raise ErroAmazon(f"Não alcancei a Amazon: {mascarar(e)}", 502)
        if r.status_code == 401 and not renovou:
            renovou = True
            access_token(conta_id, forcar=True)
            continue
        if r.status_code == 429:
            time.sleep(espera)
            espera = min(espera * 2, 30)
            continue
        break
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


def request(conta_id: str, metodo: str, caminho: str, **params):
    return _enviar(conta_id, metodo, caminho, params=params)


def get(conta_id: str, caminho: str, **params):
    return _enviar(conta_id, "GET", caminho, params=params)


def post(conta_id: str, caminho: str, corpo: dict):
    return _enviar(conta_id, "POST", caminho, corpo=corpo)


def falha(status: int, corpo) -> ErroAmazon:
    """Traduz uma resposta não-200 em erro com a explicação que o usuário precisa."""
    detalhe = mascarar(corpo if isinstance(corpo, str) else json.dumps(corpo, ensure_ascii=False))[:300]
    if status == 403:
        return ErroAmazon(f"Sem permissão (403): o role deste módulo ainda não foi concedido ao app. {detalhe}", 403)
    if status == 401:
        return ErroAmazon(f"Token recusado (401): {detalhe}", 401)
    return ErroAmazon(f"Amazon respondeu {status}: {detalhe}", 502)


# ─── Dados ───────────────────────────────────────────────────────────────────

def estoque_fba(conta_id: str) -> list:
    """Inventário FBA consolidado por ASIN. Falha de qualquer página aborta — nunca devolve lista cortada."""
    c = conta_cfg(conta_id)
    mk = c.get("marketplace_id") or MARKETPLACE_BR
    descontinuados = set(c.get("asins_descontinuados", []))

    linhas, pagina = [], None
    while True:
        params = {"granularityType": "Marketplace", "granularityId": mk,
                  "marketplaceIds": mk, "details": "true"}
        if pagina:
            params["nextToken"] = pagina
        status, corpo = get(conta_id, "/fba/inventory/v1/summaries", **params)
        if status != 200:
            raise falha(status, corpo)
        payload = corpo.get("payload", {})
        for s in payload.get("inventorySummaries", []):
            if s.get("asin") not in descontinuados:
                linhas.append(calculos.linha_de_estoque(s))
        pagina = (payload.get("pagination") or {}).get("nextToken")
        if not pagina:
            break
    return calculos.agrupar_estoque_por_asin(linhas)


def vendas_asin(conta_id: str, asin: str, rede: str = None) -> dict:
    """
    Velocidade de venda de um ASIN nos últimos 30 dias, somando todas as logísticas.
    `rede`: "" = todas (padrão) | "AFN" = só FBA | "MFN" = só DBA.
    """
    cfg = load_config()
    c = conta_cfg(conta_id)
    par = cfg.get("parametros", {})
    rede = (par.get("rede", "") if rede is None else rede).strip().upper()
    if rede not in ("", "AFN", "MFN"):
        raise ErroAmazon("rede deve ser vazio, AFN ou MFN", 400)

    fuso = par.get("fuso", "America/Sao_Paulo")
    if fuso not in FUSOS:
        raise ErroAmazon(f"parametros.fuso deve ser um de: {', '.join(FUSOS)}", 400)

    # Só dias fechados: o dia de hoje, ainda em andamento, fica de fora.
    fim = datetime.now(FUSOS[fuso]).replace(hour=0, minute=0, second=0, microsecond=0)
    inicio = fim - timedelta(days=30)
    z = fim.strftime("%z")
    off = f"{z[:3]}:{z[3:]}"                       # -0300 -> -03:00
    fmt = "%Y-%m-%dT%H:%M:%S"
    params = {"marketplaceIds": c.get("marketplace_id") or MARKETPLACE_BR,
              "interval": f"{inicio.strftime(fmt)}{off}--{fim.strftime(fmt)}{off}",
              "granularity": "Day", "granularityTimeZone": fuso,
              "buyerType": "All", "asin": asin}
    if rede:
        params["fulfillmentNetwork"] = rede

    status, corpo = get(conta_id, "/sales/v1/orderMetrics", **params)
    if status != 200:
        raise falha(status, corpo)
    buckets = corpo if isinstance(corpo, list) else corpo.get("payload", corpo.get("data", []))

    return {"asin": asin, "conta": conta_id, "rede": rede or "todas",
            **calculos.velocidade(buckets, fim.date(),
                                  par.get("peso_7d", 0.40), par.get("peso_30d", 0.60))}
