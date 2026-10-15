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


# ─── CUSTOS REAIS PARA O SIMULADOR ───────────────────────────────────────────
# Fontes (validadas em 15/10/2026, só leitura):
#   comissão %   -> /sites/MLB/listing_prices
#   frete        -> /users/{seller}/shipping_options/free?item_price=  (= tabela de Custos
#                   dos Envios do ML, ajuda/40538; já considera peso e reputação)
#   campanhas    -> /seller-promotions/items/{mlb}  (nome, tipo, meli_percentage)
#   última venda -> /orders/search + /shipments/{id}[/costs]
# No Full/Coleta/Agências não há taxa fixa à parte: o que varia por peso/preço é o frete.
# Flex (self_service) fica de fora da "última venda" para não contaminar o frete.

LOGISTICA_FORA = {"self_service"}


def escolher_campanha(promos: list, preco_ref: float) -> Optional[dict]:
    """Entre as campanhas em andamento, a que melhor explica o preço praticado
    (menor distância entre o preço da campanha e o preço de referência)."""
    ativas = [p for p in promos if p.get("status") == "started" and (p.get("price") or 0) > 0]
    if not ativas:
        return None
    return min(ativas, key=lambda p: abs((p.get("price") or 0) - preco_ref))


def rebot_unitario(promo: Optional[dict]) -> float:
    """Rebot/un = meli_percentage × preço cheio (original_price) da campanha."""
    if not promo:
        return 0.0
    pct = promo.get("meli_percentage") or 0
    base = promo.get("original_price") or 0
    return round(pct / 100 * base, 2) if pct > 0 and base > 0 else 0.0


def _resumo_promo(p: dict) -> dict:
    return {
        "id":        p.get("id"),
        "tipo":      p.get("type"),
        "subtipo":   p.get("sub_type"),
        "nome":      p.get("name") or "",
        "status":    p.get("status"),
        "preco":     p.get("price"),
        "preco_cheio": p.get("original_price"),
        "meli_pct":  p.get("meli_percentage"),
        "inicio":    (p.get("start_date") or "")[:10],
        "fim":       (p.get("finish_date") or "")[:10],
        "rebot_un":  rebot_unitario(p),
    }


async def _get_json(client, conta: str, path: str):
    try:
        r = await client.get(f"{ML_BASE_URL}{path}", headers=_headers(conta))
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


async def frete_para_preco(conta: str, mlb: str, preco: float) -> dict:
    """Frete do vendedor para o anúncio a um dado preço (muda por faixa: 18,99/48,99/78,99...)."""
    seller_id = get_seller_id(conta)
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        d = await _get_json(client, conta,
            f"/users/{seller_id}/shipping_options/free?item_id={mlb}&item_price={preco}")
    cov = ((d or {}).get("coverage") or {}).get("all_country") or {}
    if "list_cost" not in cov:
        return {"success": False, "error": "frete indisponível"}
    return {"success": True, "frete": cov["list_cost"], "peso_g": cov.get("billable_weight"),
            "preco": preco}


async def _ultima_venda(client, conta: str, mlb: str, comissao_pct: float) -> Optional[dict]:
    """Última venda paga que NÃO seja Flex. Preço, comissão líquida e frete por unidade."""
    seller_id = get_seller_id(conta)
    ate = datetime.now().strftime("%Y-%m-%d")
    de  = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    d = await _get_json(client, conta,
        f"/orders/search?seller={seller_id}&item={mlb}&order.status=paid"
        f"&order.date_created.from={de}T00:00:00.000-04:00"
        f"&order.date_created.to={ate}T23:59:59.000-04:00&limit=20&sort=date_desc")
    for od in (d or {}).get("results", []):
        sid = (od.get("shipping") or {}).get("id")
        item = next((oi for oi in od.get("order_items", []) if oi["item"]["id"] == mlb), None)
        if not sid or not item:
            continue
        sh = await _get_json(client, conta, f"/shipments/{sid}")
        logistica = (sh or {}).get("logistic_type")
        if not sh or logistica in LOGISTICA_FORA:
            continue
        custos = await _get_json(client, conta, f"/shipments/{sid}/costs")
        frete_envio = ((custos or {}).get("senders") or [{}])[0].get("cost")
        qtd   = item.get("quantity") or 1
        preco = item.get("unit_price") or 0
        sale_fee = item.get("sale_fee") or 0
        cheia = round(comissao_pct * preco, 2)
        return {
            "data":       od.get("date_created", "")[:10],
            "order_id":   od.get("id"),
            "preco":      preco,
            "quantidade": qtd,
            "logistica":  logistica,
            "sale_fee_un": sale_fee,
            "tarifa_cheia_un": cheia,
            "rebot_real_un": round(max(0.0, cheia - sale_fee), 2),
            "frete_un":   round(frete_envio / qtd, 2) if frete_envio is not None else None,
        }
    return None


async def custos_anuncio(conta: str, mlb: str, preco: Optional[float] = None) -> dict:
    """Custos reais para pré-preencher o simulador: última venda (Full/Coleta/Agências),
    com o anúncio como alternativa, e as campanhas em andamento."""
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        item = await _get_json(client, conta, f"/items/{mlb}")
        if not item:
            return {"success": False, "error": "anúncio não encontrado"}
        lp = await _get_json(client, conta,
            f"/sites/MLB/listing_prices?price={item.get('price')}"
            f"&listing_type_id={item.get('listing_type_id')}&category_id={item.get('category_id')}")
        row = (lp[0] if isinstance(lp, list) and lp else lp) or {}
        pct = ((row.get("sale_fee_details") or {}).get("percentage_fee") or 0) / 100
        if pct <= 0:
            return {"success": False, "error": "comissão indisponível"}

        venda = await _ultima_venda(client, conta, mlb, pct)
        preco_ref = preco or (venda or {}).get("preco") or item.get("price") or 0
        promos = await _get_json(client, conta, f"/seller-promotions/items/{mlb}?app_version=v2")

    frete = await frete_para_preco(conta, mlb, preco_ref)
    promos = promos if isinstance(promos, list) else []
    ativa = escolher_campanha(promos, preco_ref)
    return {
        "success": True,
        "mlb": mlb,
        "fonte": "ultima_venda" if venda else "anuncio",
        "preco": preco_ref,
        "comissao_pct": pct,
        "frete": frete.get("frete") if frete["success"] else None,
        "peso_g": frete.get("peso_g"),
        "ultima_venda": venda,
        "campanha_ativa": _resumo_promo(ativa) if ativa else None,
        "campanhas": [_resumo_promo(p) for p in promos if p.get("status") in ("started", "pending", "candidate")],
        "rebot_un": rebot_unitario(ativa),
    }
