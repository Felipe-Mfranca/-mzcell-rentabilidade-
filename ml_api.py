"""
ml_api.py — Integração com API do Mercado Livre
MZCell Rentabilidade — com suporte a PKCE
"""

import httpx
import json
import os
import base64
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
PKCE_PATH   = os.path.join(os.path.dirname(__file__), "pkce_temp.json")
ML_BASE_URL = "https://api.mercadolibre.com"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(config: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

def get_token(conta: str) -> Optional[str]:
    return load_config()["contas"].get(conta, {}).get("access_token", "")

def get_seller_id(conta: str) -> Optional[str]:
    return load_config()["contas"].get(conta, {}).get("seller_id", "")

def _headers(conta: str) -> dict:
    return {"Authorization": f"Bearer {get_token(conta)}"}


# ─── PKCE ─────────────────────────────────────────────────────────────────────

def gerar_pkce() -> dict:
    """Gera code_verifier e code_challenge para PKCE."""
    code_verifier  = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode()
    digest         = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b'=').decode()
    return {"code_verifier": code_verifier, "code_challenge": code_challenge}

def salvar_pkce(conta: str, pkce: dict):
    data = {}
    if os.path.exists(PKCE_PATH):
        with open(PKCE_PATH, "r") as f:
            try: data = json.load(f)
            except: data = {}
    data[conta] = pkce
    with open(PKCE_PATH, "w") as f:
        json.dump(data, f)

def carregar_pkce(conta: str) -> Optional[dict]:
    if not os.path.exists(PKCE_PATH):
        return None
    with open(PKCE_PATH, "r") as f:
        try: return json.load(f).get(conta)
        except: return None


# ─── TOKEN ────────────────────────────────────────────────────────────────────

def gerar_url_autorizacao(conta: str) -> str:
    """Gera URL de autorização ML com PKCE."""
    config = load_config()
    c      = config["contas"].get(conta, {})
    pkce   = gerar_pkce()
    salvar_pkce(conta, pkce)

    url = (
        f"https://auth.mercadolivre.com.br/authorization"
        f"?response_type=code"
        f"&client_id={c['client_id']}"
        f"&redirect_uri={c['redirect_uri']}"
        f"&code_challenge={pkce['code_challenge']}"
        f"&code_challenge_method=S256"
    )
    return url


async def autorizar_conta(conta: str, code: str) -> dict:
    """Troca o código TG-... pelo access_token usando PKCE."""
    config = load_config()
    c      = config["contas"].get(conta)
    if not c:
        return {"success": False, "error": f"Conta {conta} não encontrada"}

    pkce = carregar_pkce(conta)
    if not pkce:
        return {"success": False, "error": "code_verifier não encontrado. Refaça a autorização."}

    payload = {
        "grant_type":    "authorization_code",
        "client_id":     c["client_id"],
        "client_secret": c["client_secret"],
        "code":          code,
        "redirect_uri":  c["redirect_uri"],
        "code_verifier": pkce["code_verifier"],
    }

    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(f"{ML_BASE_URL}/oauth/token", data=payload)

    if resp.status_code != 200:
        return {"success": False, "error": resp.text}

    data = resp.json()
    config["contas"][conta]["access_token"]    = data["access_token"]
    config["contas"][conta]["refresh_token"]   = data.get("refresh_token", "")
    config["contas"][conta]["seller_id"]       = str(data.get("user_id", ""))
    config["contas"][conta]["token_expires_at"] = (
        datetime.utcnow() + timedelta(seconds=data.get("expires_in", 21600))
    ).isoformat()
    save_config(config)
    return {"success": True, "seller_id": data.get("user_id")}


async def refresh_token(conta: str) -> dict:
    config = load_config()
    c = config["contas"].get(conta)
    if not c:
        return {"success": False, "error": f"Conta {conta} não encontrada"}
    payload = {
        "grant_type":    "refresh_token",
        "client_id":     c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": c["refresh_token"],
    }
    async with httpx.AsyncClient(verify=False) as client:
        resp = await client.post(f"{ML_BASE_URL}/oauth/token", data=payload)
    if resp.status_code != 200:
        return {"success": False, "error": resp.text}
    data = resp.json()
    config["contas"][conta]["access_token"]    = data["access_token"]
    config["contas"][conta]["refresh_token"]   = data.get("refresh_token", c["refresh_token"])
    config["contas"][conta]["token_expires_at"] = (
        datetime.utcnow() + timedelta(seconds=data.get("expires_in", 21600))
    ).isoformat()
    save_config(config)
    return {"success": True, "expires_in": data.get("expires_in")}


# ─── PRODUTOS ────────────────────────────────────────────────────────────────

async def listar_produtos(conta: str) -> dict:
    seller_id = get_seller_id(conta)
    if not seller_id:
        return {"success": False, "error": "seller_id não configurado"}
    produtos = []
    scroll_id = None
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        while True:
            url = f"{ML_BASE_URL}/users/{seller_id}/items/search?status=active&limit=100"
            if scroll_id:
                url += f"&scroll_id={scroll_id}"
            resp = await client.get(url, headers=_headers(conta))
            if resp.status_code == 401:
                return {"success": False, "error": "Token expirado", "code": 401}
            if resp.status_code != 200:
                break
            data = resp.json()
            items = data.get("results", [])
            if not items:
                break
            produtos.extend(items)
            scroll_id = data.get("scroll_id")
            if not scroll_id:
                break
    return {"success": True, "produtos": produtos, "total": len(produtos)}


async def detalhes_multiplos(conta: str, mlbs: list) -> dict:
    resultados = {}
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        for i in range(0, len(mlbs), 20):
            ids = ",".join(mlbs[i:i+20])
            resp = await client.get(f"{ML_BASE_URL}/items?ids={ids}", headers=_headers(conta))
            if resp.status_code == 200:
                for item in resp.json():
                    if item.get("code") == 200:
                        body = item.get("body", {})
                        resultados[body.get("id")] = body
    return {"success": True, "data": resultados}


# ─── VENDAS ──────────────────────────────────────────────────────────────────

async def orders_produto(conta: str, mlb: str, date_from: str, date_to: str) -> dict:
    """
    Busca TODOS os pedidos via paginação completa (offset incremental).
    O filtro de status é feito em processar_orders, não na URL,
    porque a API ML não suporta todos os valores via query param.
    """
    seller_id = get_seller_id(conta)
    base_url = (
        f"{ML_BASE_URL}/orders/search"
        f"?seller={seller_id}&item={mlb}"
        f"&order.date_created.from={date_from}T00:00:00.000-04:00"
        f"&order.date_created.to={date_to}T23:59:59.000-04:00"
        f"&limit=50&sort=date_desc"
    )
    todos_resultados = []
    offset = 0
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        while True:
            resp = await client.get(f"{base_url}&offset={offset}", headers=_headers(conta))
            if resp.status_code != 200:
                return {"success": False, "error": resp.text}
            data = resp.json()
            resultados = data.get("results", [])
            if not resultados:
                break
            todos_resultados.extend(resultados)
            total = data.get("paging", {}).get("total", 0)
            offset += 50
            if offset >= total:
                break
    # Retorna estrutura compatível com processar_orders
    return {"success": True, "data": {"results": todos_resultados}}


async def visitas_produto(conta: str, mlb: str, date_from: str, date_to: str) -> dict:
    url = f"{ML_BASE_URL}/items/{mlb}/visits?date_from={date_from}&date_to={date_to}"
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        resp = await client.get(url, headers=_headers(conta))
    if resp.status_code != 200:
        return {"success": False, "error": resp.text}
    data = resp.json()
    return {
        "success": True,
        "data": {
            # ATENÇÃO: o campo `total_visits` da API = "Visitas Únicas" do ML Seller Center.
            # O "Total de visitas" do ML (page views, sempre maior) NÃO é exposto pela API pública.
            "total_visits":  data.get("total_visits", 0),
            "unique_visits": data.get("total_visits", 0),   # alias — são a mesma coisa
        },
    }


async def visitas_diarias(conta: str, mlb: str, date_from: str, date_to: str) -> dict:
    """
    Busca visitas reais por dia via /visits/time_window.
    A API retorna datas em UTC; usamos o string de data diretamente para match com o período.
    Retorna {date_str: total} apenas para dias dentro do intervalo [date_from, date_to].
    """
    from datetime import datetime as _dt
    dias = (_dt.fromisoformat(date_to) - _dt.fromisoformat(date_from)).days + 2
    url = f"{ML_BASE_URL}/items/{mlb}/visits/time_window?last={dias + 3}&unit=day"
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        resp = await client.get(url, headers=_headers(conta))
    if resp.status_code != 200:
        return {"success": False, "error": resp.text}
    data = resp.json()
    resultado: dict[str, int] = {}
    for entry in data.get("results", []):
        raw = (entry.get("date") or "")[:10]
        if date_from <= raw <= date_to:
            resultado[raw] = entry.get("total", 0)
    return {"success": True, "data": resultado}


# ─── ADS ─────────────────────────────────────────────────────────────────────

async def metricas_ads(conta: str, mlb: str, date_from: str, date_to: str) -> dict:
    site_id = mlb.rstrip("0123456789")  # MLB6161189080 → MLB
    headers = {**_headers(conta), "api-version": "2"}
    metrics_param = (
        "clicks,prints,ctr,cost,cpc,acos,roas,"
        "direct_amount,indirect_amount,total_amount,units_quantity"
    )

    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        r = await client.get(
            f"{ML_BASE_URL}/marketplace/advertising/{site_id}/product_ads/ads/{mlb}"
            f"?date_from={date_from}&date_to={date_to}&metrics={metrics_param}",
            headers=headers
        )
        if r.status_code == 404:
            return {"success": False, "error": "organico", "data": {}, "is_organic": True}
        if r.status_code != 200:
            return {"success": False, "error": f"ADS erro: {r.status_code}", "data": {}}

        info = r.json()
        campaign_id = info.get("campaign_id")
        if not campaign_id:
            return {"success": False, "error": "organico", "data": {}, "is_organic": True}

        m = info.get("metrics", {})

        # Busca roas_target, acos_target, budget e strategy da campanha
        r2 = await client.get(
            f"{ML_BASE_URL}/advertising/product_ads/campaigns/{campaign_id}",
            headers=headers
        )
        camp = r2.json() if r2.status_code == 200 else {}

        return {
            "success": True,
            "data": {
                "campaign_id":          campaign_id,
                "catalog_listing":      info.get("catalog_listing", False),
                "ads_total":            m.get("cost", 0.0),
                "cliques":              m.get("clicks", 0),
                "impressoes":           m.get("prints", 0),
                "tacos":                m.get("acos", 0.0),
                "ctr":                  m.get("ctr", 0.0),
                "roas":                 m.get("roas", 0.0),
                "cpc":                  m.get("cpc", 0.0),
                "vendas_publi":         m.get("direct_units_quantity", 0),
                "vendas_indiretas":     m.get("indirect_units_quantity", 0),
                "vendas_publi_total":   m.get("units_quantity", 0),
                "receita_publi":        m.get("direct_amount", 0.0),
                "receita_publi_direta": m.get("direct_amount", 0.0),
                "receita_publi_total":  m.get("total_amount", 0.0),
                "roas_target":          camp.get("roas_target", 0.0),
                "acos_target":          camp.get("acos_target", 0.0),
                "budget":               camp.get("budget", 0.0),
                "strategy":             camp.get("strategy", ""),
            }
        }


async def campanhas_ads(conta: str) -> dict:
    """Lista campaign_ids de todos os itens ativos."""
    seller_id = get_seller_id(conta)
    headers = {**_headers(conta), "api-version": "2"}
    url = f"{ML_BASE_URL}/advertising/product_ads/campaigns?seller_id={seller_id}"
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        return {"success": False, "error": resp.text}
    return {"success": True, "data": resp.json()}


async def get_catalog_listing(conta: str, mlb: str) -> dict:
    """
    Consulta apenas o endpoint /advertising/product_ads/items/{mlb}
    para obter catalog_listing e variacoes_count sem precisar de datas.
    """
    headers = {**_headers(conta), "api-version": "2"}
    async with httpx.AsyncClient(timeout=20, verify=False) as client:
        r = await client.get(
            f"{ML_BASE_URL}/advertising/product_ads/items/{mlb}",
            headers=headers,
        )
    if r.status_code != 200:
        return {"success": False, "error": f"status {r.status_code}"}
    data = r.json()
    return {
        "success": True,
        "catalog_listing": data.get("catalog_listing", False),
        "variacoes_count": len(data.get("variations", [])),
    }
