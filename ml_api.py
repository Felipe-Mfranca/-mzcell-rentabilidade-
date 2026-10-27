"""
ml_api.py — Integração com API do Mercado Livre
MZ App — com suporte a PKCE
"""

import httpx
import json
import os
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
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


async def vinculo_catalogo(conta: str, catalog_product_id: str, mlb: str) -> dict:
    """Anúncio vencedor do catálogo e se este MLB é ele (ou só está vinculado a ele)."""
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        d = await _get_json(client, conta, f"/products/{catalog_product_id}/items")
    res = (d or {}).get("results") or []
    vencedor = next((r for r in res if r.get("buy_box_winner")), res[0] if res else None)
    vencedor_id = (vencedor or {}).get("item_id")
    return {"catalog_product_id": catalog_product_id, "vencedor": vencedor_id,
            "eh_vencedor": vencedor_id == mlb}


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
    dias = max((_dt.fromisoformat(date_to) - _dt.fromisoformat(date_from)).days,
               (_dt.now() - _dt.fromisoformat(date_from)).days) + 2
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
        "direct_amount,indirect_amount,total_amount,units_quantity,organic_units_quantity"
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
        # endpoint antigo (/advertising/product_ads/campaigns/{id}) devolve 404; o atual é o da API marketplace
        r2 = await client.get(
            f"{ML_BASE_URL}/marketplace/advertising/{site_id}/product_ads/campaigns/{campaign_id}",
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
                "vendas_organicas":     m.get("organic_units_quantity", 0),
                "receita_publi":        m.get("total_amount", 0.0),          # direta + indireta: base do ROAS do ML
                "receita_publi_direta": m.get("direct_amount", 0.0),
                "receita_publi_total":  m.get("total_amount", 0.0),
                "roas_target":          camp.get("roas_target", 0.0),
                "acos_target":          camp.get("acos_target", 0.0),
                "budget":               camp.get("budget", 0.0),
                "strategy":             camp.get("strategy", ""),
            }
        }


async def metricas_ads_bruto(conta: str, mlb: str, date_from: str, date_to: str) -> dict:
    """Métricas de ADS do anúncio no intervalo (uma consulta). Um dia por chamada dá o gasto real do dia.
    404 = sem campanha (devolve {}). Erro de rede ou status diferente de 200 levanta exceção (nunca vira zero)."""
    import asyncio as _aio
    site_id = mlb.rstrip("0123456789")
    headers = {**_headers(conta), "api-version": "2"}
    metrics_param = ("clicks,prints,ctr,cost,cpc,acos,roas,"
                     "direct_amount,indirect_amount,total_amount,units_quantity,organic_units_quantity")
    url = (f"{ML_BASE_URL}/marketplace/advertising/{site_id}/product_ads/ads/{mlb}"
           f"?date_from={date_from}&date_to={date_to}&metrics={metrics_param}")
    ultimo = None
    for tentativa in range(4):
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                r = await client.get(url, headers=headers)
        except httpx.HTTPError as e:
            ultimo = f"rede: {type(e).__name__}"
            await _aio.sleep(1.5 * (tentativa + 1))
            continue
        if r.status_code == 404:
            return {}
        if r.status_code == 200:
            return r.json().get("metrics", {}) or {}
        ultimo = f"HTTP {r.status_code}"
        if r.status_code in (429, 500, 502, 503, 504):
            await _aio.sleep(1.5 * (tentativa + 1))
            continue
        break
    raise RuntimeError(f"ADS {mlb} {date_from}: {ultimo}")


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
    site_id = mlb.rstrip("0123456789")   # endpoint antigo (/advertising/product_ads/items) devolve 404; o atual é o da API marketplace
    async with httpx.AsyncClient(timeout=20, verify=False) as client:
        r = await client.get(
            f"{ML_BASE_URL}/marketplace/advertising/{site_id}/product_ads/ads/{mlb}",
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


NOSSAS = {"SELLER_CAMPAIGN", "PRICE_DISCOUNT", "PROPRIA", "SEM_PROMOCAO"}


def campanha_efetiva(promos: list, sale_price: Optional[dict]) -> Optional[dict]:
    """Campanha que está aplicada AGORA, pelo id que o /sale_price informa (exato)."""
    meta = (sale_price or {}).get("metadata") or {}
    pid, cid = meta.get("promotion_id"), meta.get("campaign_id")
    if not (pid or cid):
        return None
    return next((p for p in promos if p.get("status") == "started"
                 and (p.get("ref_id") == pid or p.get("id") == cid)), None)


def melhor_oportunidade(promos: list, atual_preco: float, cheio: float, pct: float,
                        ativa: Optional[dict], descartadas: Optional[list] = None) -> Optional[dict]:
    """Candidata que rende mais líquido/un que a situação atual (mesma regra do rebot_agenda).
    líquido = preço − (tarifa% × preço − rebot); rebot = meli% × preço cheio da campanha.
    Empate de preço e líquido: só vale se trocar uma campanha nossa por uma do ML."""
    a_tipo = (ativa or {}).get("type") or "SEM_PROMOCAO"
    a_rebot = rebot_unitario(ativa)
    a_liq = atual_preco - (pct * atual_preco - a_rebot)
    melhor = None
    for p in promos:
        if p.get("status") != "candidate" or not p.get("price"):
            continue
        preco = p["price"]
        rebot = (p.get("meli_percentage") or 0) / 100 * (p.get("original_price") or cheio)
        liq = preco - (pct * preco - rebot)
        ganho, dp = round(liq - a_liq, 2), round(preco - atual_preco, 2)
        empate = abs(ganho) < 0.01 and abs(dp) < 0.01
        nome_c = p.get("name") or p.get("type")
        if empate:
            if not (a_tipo in NOSSAS and p.get("type") not in NOSSAS):
                if descartadas is not None: descartadas.append({"nome": nome_c, "motivo": "empate: mesmo preço e líquido de hoje"})
                continue
        elif ganho <= 0:
            if descartadas is not None: descartadas.append({"nome": nome_c, "motivo": "o líquido não melhora"})
            continue
        if dp > 0:
            # aumento de preço não é oportunidade: a promoção existe para vender mais (regra do usuário)
            if descartadas is not None: descartadas.append({"nome": nome_c, "motivo": f"aumenta o preço em R$ {dp:.2f}".replace(".", ",")})
            continue
        if dp > 0 and rebot - a_rebot <= 0.005:
            continue   # ganho vem só do preço mais alto: rebot é desconto pago pelo ML, isso não é rebot
        o = {"tipo": p.get("type"), "nome": p.get("name") or p.get("type"),
             "preco": preco, "rebot": round(rebot, 2), "ganho_un": ganho,
             "delta_preco": dp, "empate": empate,
             "fim": (p.get("finish_date") or "")[:10], "estoque": p.get("stock"),
             "atual_nome": (ativa or {}).get("name") or ("sem promoção" if a_tipo == "SEM_PROMOCAO" else a_tipo),
             "atual_preco": atual_preco, "atual_rebot": a_rebot}
        if melhor is None or ganho > melhor["ganho_un"]:
            melhor = o
    return melhor


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
        sp_inicial = await _get_json(client, conta, f"/items/{mlb}/sale_price?context=channel_marketplace")
        preco_taxa = (sp_inicial or {}).get("amount") or item.get("price")   # comissão do preço vigente
        lp = await _get_json(client, conta,
            f"/sites/MLB/listing_prices?price={preco_taxa}"
            f"&listing_type_id={item.get('listing_type_id')}&category_id={item.get('category_id')}")
        row = (lp[0] if isinstance(lp, list) and lp else lp) or {}
        pct = ((row.get("sale_fee_details") or {}).get("percentage_fee") or 0) / 100
        if pct <= 0:
            return {"success": False, "error": "comissão indisponível"}

        venda = await _ultima_venda(client, conta, mlb, pct)
        promos = await _get_json(client, conta, f"/seller-promotions/items/{mlb}?app_version=v2")
        sp = await _get_json(client, conta, f"/items/{mlb}/sale_price?context=channel_marketplace")
        # preço vigente (com promoção) antes do preço cheio do anúncio: item.price é o cheio quando há promoção
        # preço vigente (com promoção) primeiro; o preço da última venda é só referência
        preco_ref = preco or (sp or {}).get("amount") or item.get("price") or (venda or {}).get("preco") or 0

    frete = await frete_para_preco(conta, mlb, preco_ref)
    promos = promos if isinstance(promos, list) else []
    atual_preco = (sp or {}).get("amount") or item.get("price") or preco_ref
    cheio = (sp or {}).get("regular_amount") or item.get("price") or preco_ref
    ativa_hoje = campanha_efetiva(promos, sp)
    ativa = ativa_hoje or escolher_campanha(promos, preco_ref)
    descartadas = []
    oportunidade = melhor_oportunidade(promos, atual_preco, cheio, pct, ativa_hoje, descartadas)
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
        "oportunidade": oportunidade,
        "nao_vale": descartadas[:3] if not oportunidade else [],
    }


# ─── SKU do anúncio ──────────────────────────────────────────────────────────
# O SKU do vendedor costuma estar no atributo SELLER_SKU; seller_custom_field vem vazio na maioria dos anúncios.
# Chave para cruzar com o Bling. Anúncio com variações pode ter um SKU por variação.

def _attr_sku(attrs) -> str:
    for a in attrs or []:
        if a.get("id") == "SELLER_SKU" and a.get("value_name"):
            return str(a["value_name"]).strip()
    return ""


def sku_do_anuncio(item: dict) -> str:
    """SKU do anúncio: seller_custom_field, senão atributo SELLER_SKU, senão o das variações (distintos juntos por ' / ')."""
    sku = (item.get("seller_custom_field") or "").strip() or _attr_sku(item.get("attributes"))
    if sku:
        return sku
    vistos = []
    for v in item.get("variations") or []:
        sv = (v.get("seller_custom_field") or "").strip() or _attr_sku(v.get("attributes"))
        if sv and sv not in vistos:
            vistos.append(sv)
    return " / ".join(vistos)


async def skus_anuncios(conta: str, mlbs: list) -> dict:
    """{mlb: sku} de uma lista de anúncios (multiget de 20 em 20). Só leitura."""
    saida = {}
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        for i in range(0, len(mlbs), 20):
            lote = mlbs[i:i + 20]
            d = await _get_json(client, conta,
                f"/items?ids={','.join(lote)}&attributes=id,seller_custom_field,attributes,variations")
            for e in d or []:
                if e.get("code") == 200:
                    b = e["body"]
                    saida[b["id"]] = sku_do_anuncio(b)
    return saida


# ─── SAC: contestações de devolução (somente leitura) ────────────────────────
# Desfecho de uma contestação (etapa 'recontact'), validado em 16/10/2026 com 9 casos reais:
#   A) reversão no pedido  : 2º item em collection.refunds com status_detail 'bpp_covered'
#   B) reembolso direto    : crédito money_transfer "Entrada de dinheiro correspondente a sua reclamação"
#                            no mesmo minuto da decisão (nº da operação do Mercado Pago)
#   C) recusado            : 1 estorno só e texto "não poderemos ressarci-lo / valor mantido com o comprador"
# O crédito B não traz o ID da reclamação: o vínculo é por valor + hora (confiança "provável").
import asyncio
import html as _html
import re

_RECUSA = ("não poderemos ressarci", "nao poderemos ressarci", "mantido na conta do seu comprador",
           "dinheiro na conta do seu comprador", "pagamento disponível na conta do comprador",
           "deixamos o pagamento disponível", "impactará no cálculo", "impactara no calculo",
           "reafirma o problema")
_REVERSAO = ("reembolsamos o valor", "você vai receber o reembolso", "voce vai receber o reembolso")
_CRED_REC = ("reclama", "garantida", "proteção do mercado envios full", "protecao do mercado envios full")


def sac_texto(msg: Optional[str]) -> str:
    """HTML do mediador -> texto simples. Mensagem codificada (base64) vira ''."""
    if not msg or re.fullmatch(r"[A-Za-z0-9+/=\s]{200,}", msg):
        return ""
    return re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", msg))).strip()


def sac_prazo_texto(msgs: list) -> Optional[str]:
    """Prazo escrito pelo mediador ('...até o dia 15/07'); a API não tem due_date."""
    for m in sorted(msgs, key=lambda x: x.get("date_created", ""), reverse=True):
        if m.get("sender_role") != "mediator":
            continue
        achou = re.search(r"at[ée] o dia (\d{1,2}/\d{1,2})", sac_texto(m.get("message")), re.I)
        if achou:
            return achou.group(1)
    return None


def sac_custo_etiqueta(msgs: list) -> Optional[float]:
    """Valor da etiqueta de devolução que o mediador diz ficar com o vendedor."""
    for m in sorted(msgs, key=lambda x: x.get("date_created", "")):
        if m.get("sender_role") != "mediator":
            continue
        achou = re.search(r"etiqueta de devolu[çc][ãa]o (?:é |de )?(?:de )?R\$\s?([\d.]+,\d{2})",
                          sac_texto(m.get("message")))
        if achou:
            return float(achou.group(1).replace(".", "").replace(",", "."))
    return None


def _minutos(a: Optional[str], b: Optional[str]) -> float:
    """Diferença em minutos entre dois ISO com fuso (ex.: 2026-10-06T13:57:53.000-04:00)."""
    try:
        da = datetime.fromisoformat(a.replace("Z", "+00:00"))
        db = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return (da - db).total_seconds() / 60
    except Exception:
        return 1e9


def sac_operacao_texto(msgs: list) -> Optional[dict]:
    """Reembolso direto: o mediador escreve o valor e o número da operação do Mercado Pago na mensagem."""
    for m in sorted(msgs, key=lambda x: x.get("date_created", ""), reverse=True):
        if m.get("sender_role") != "mediator":
            continue
        t = sac_texto(m.get("message"))
        op = re.search(r"n[úu]mero de opera[çc][ãa]o\W{0,4}(\d{9,})", t, re.I)
        if op:
            val = re.search(r"reembolso de R\$\s?([\d.]+,\d{2})", t)
            return {"operacao": int(op.group(1)), "quando": m.get("date_created"),
                    "valor": float(val.group(1).replace(".", "").replace(",", ".")) if val else None}
    return None


def sac_desfecho(collection: Optional[dict], msgs: list, creditos: Optional[list] = None,
                 valores: Optional[list] = None) -> dict:
    """Classifica uma contestação. `creditos` = [{id,data,valor,motivo}] da conta; `valores` = valores
    que um reembolso direto poderia ter (venda, etiqueta)."""
    refunds = (collection or {}).get("refunds") or []
    cobertos = [r for r in refunds if (r.get("metadata") or {}).get("status_detail") == "bpp_covered"]
    if cobertos:
        return {"desfecho": "REVERTIDO_NO_PEDIDO", "confianca": "confirmado",
                "quando": cobertos[-1].get("date_created"), "valor": cobertos[-1].get("amount"),
                "operacao": None, "evidencia": "estorno bpp_covered no pagamento (saldo da venda ajustado)"}
    mediador = [m for m in sorted(msgs, key=lambda x: x.get("date_created", "")) if m.get("sender_role") == "mediator"]
    textos = [(m.get("date_created", ""), sac_texto(m.get("message")).lower()) for m in mediador]
    legiveis = [(q, t) for q, t in textos if t]
    texto_op = sac_operacao_texto(msgs)
    if texto_op:
        return {"desfecho": "REVERTIDO_REEMBOLSO", "confianca": "confirmado", "quando": texto_op["quando"],
                "valor": texto_op["valor"], "operacao": texto_op["operacao"],
                "evidencia": "o mediador informou o número da operação do Mercado Pago"}
    if creditos and legiveis:
        alvo = legiveis[-1][0]
        for c in creditos:
            if abs(_minutos(c.get("data"), alvo)) <= 30 and \
               any(abs((c.get("valor") or 0) - v) < 0.01 for v in (valores or [])):
                return {"desfecho": "REVERTIDO_REEMBOLSO", "confianca": "provável", "quando": c.get("data"),
                        "valor": c.get("valor"), "operacao": c.get("id"),
                        "evidencia": "crédito do Mercado Pago no mesmo horário da decisão (vínculo por valor e hora)"}
    for quando, t in reversed(legiveis):
        if any(k in t for k in _RECUSA):
            return {"desfecho": "RECUSADO", "confianca": "confirmado", "quando": quando, "valor": None,
                    "operacao": None, "evidencia": "o mediador manteve o valor com o comprador"}
        if any(k in t for k in _REVERSAO):
            return {"desfecho": "REVERTIDO_NAO_CONFIRMADO", "confianca": "só texto", "quando": quando,
                    "valor": None, "operacao": None,
                    "evidencia": "o mediador diz que reembolsa, mas não achei estorno nem crédito"}
    return {"desfecho": "SEM_DESFECHO", "confianca": "-", "quando": textos[-1][0] if textos else None,
            "valor": None, "operacao": None, "evidencia": "sem decisão legível do mediador"}


async def _sac_creditos(client, conta: str, desde: str, max_paginas: int = 40) -> list:
    """Créditos de reclamação da conta, do mais recente para trás até `desde` (YYYY-MM-DD).
    Só vale para reembolso sem nº de operação no texto; a maioria dos lançamentos é ruído (bonificação Flex)."""
    saida, off = [], 0
    for _ in range(max_paginas):
        d = await _get_json(client, conta,
            f"/collections/search?operation_type=money_transfer&limit=50&offset={off}&sort=date_created"
            f"&criteria=desc&range=date_created&begin_date={desde}T00:00:00.000-04:00"
            f"&end_date={(datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')}T23:59:59.000-04:00")
        res = (d or {}).get("results") or []
        for r in res:
            c = r.get("collection") or {}
            motivo = str(c.get("reason") or "")
            if any(k in motivo.lower() for k in _CRED_REC):
                saida.append({"id": c.get("id"), "data": c.get("date_created"),
                              "valor": c.get("transaction_amount"), "motivo": motivo})
        off += len(res)
        if not res or off >= ((d or {}).get("paging") or {}).get("total", 0):
            break
    return saida


async def sac_contestacoes(conta: str, limite: int = 30, abertas: bool = True,
                           de: Optional[str] = None, ate: Optional[str] = None) -> dict:
    """Contestações (etapa recontact) mais recentes, com desfecho. Só leitura: não marca nada como lido."""
    sem = asyncio.Semaphore(4)
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        lista = []
        for st in (["opened", "closed"] if abertas else ["closed"]):
            d = await _get_json(client, conta,
                f"/post-purchase/v1/claims/search?stage=recontact&status={st}"
                f"&limit=50&sort=date_created:desc")
            lista += (d or {}).get("data") or []
        lista = sorted(lista, key=lambda x: x.get("date_created", ""), reverse=True)
        lista = [c for c in lista if _no_periodo(c.get("date_created"), de, ate)][:limite]
        if not lista:
            return {"success": True, "conta": conta, "itens": [], "resumo": {}}
        desde = max(min(c["date_created"][:10] for c in lista),
                    (datetime.now() - timedelta(days=75)).strftime("%Y-%m-%d"))
        creditos = await _sac_creditos(client, conta, desde)

        async def um(c):
            async with sem:
                cid, oid = c["id"], c["resource_id"]
                o, msgs, ret, rc = await asyncio.gather(
                    _get_json(client, conta, f"/orders/{oid}"),
                    _get_json(client, conta, f"/post-purchase/v1/claims/{cid}/messages"),
                    _get_json(client, conta, f"/post-purchase/v2/claims/{cid}/returns"),
                    _get_json(client, conta, f"/post-purchase/v1/claims/{cid}/charges/return-cost"))
                o = o or {}
                msgs = msgs if isinstance(msgs, list) else []
                pay = (o.get("payments") or [{}])[0]
                col = await _get_json(client, conta, f"/collections/{pay['id']}") if pay.get("id") else None
                it = (o.get("order_items") or [{}])[0]
                venda = o.get("total_amount") or 0
                etiqueta = sac_custo_etiqueta(msgs)
                custo_ep = (rc or {}).get("amount")
                valores = [v for v in (venda, etiqueta, custo_ep, pay.get("transaction_amount")) if v]
                res = sac_desfecho(col, msgs, creditos, valores)
                if res["desfecho"] == "SEM_DESFECHO" and any(m.get("sender_role") == "respondent" for m in msgs):
                    res = {**res, "desfecho": "EM_ANALISE", "evidencia": "você contestou e o ML ainda não decidiu"}
                ultima = max((m.get("date_created", "") for m in msgs), default=c.get("last_updated", ""))
                return {
                    "claim_id": cid, "conta": conta, "pedido": oid, "mlb": (it.get("item") or {}).get("id"),
                    "titulo": (it.get("item") or {}).get("title"), "motivo_id": c.get("reason_id"),
                    "etapa": c.get("stage"), "status": c.get("status"),
                    "aberta_em": c.get("date_created"), "atualizada_em": c.get("last_updated"),
                    "valor_venda": venda, "custo_devolucao_api": custo_ep, "custo_etiqueta_texto": etiqueta,
                    "devolucao": (ret or {}).get("status"), "dinheiro": (ret or {}).get("status_money"),
                    "prazo_texto": sac_prazo_texto(msgs),
                    "anexos_vendedor": sum(len(m.get("attachments") or []) for m in msgs
                                           if m.get("sender_role") == "respondent"),
                    "msgs": len(msgs),
                    "msgs_ilegiveis": sum(1 for m in msgs if m.get("message") and not sac_texto(m["message"])),
                    "dias_parado": max(0, int(-_minutos(ultima, datetime.now().astimezone().isoformat()) // 1440)),
                    **res}
        itens = await asyncio.gather(*[um(c) for c in lista])
    resumo = {}
    for i in itens:
        resumo[i["desfecho"]] = resumo.get(i["desfecho"], 0) + 1
    return {"success": True, "conta": conta, "itens": list(itens), "resumo": resumo}

# ─── SAC: pendências (perguntas, mensagens, reclamações) — somente leitura ───
_BRT = timezone(timedelta(hours=-3))


def _data_brt(iso: Optional[str]):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_BRT).date()
    except Exception:
        return None


def _no_periodo(iso: Optional[str], de: Optional[str], ate: Optional[str]) -> bool:
    if not de and not ate:
        return True
    d = _data_brt(iso)
    if d is None:
        return False
    return (not de or d.isoformat() >= de) and (not ate or d.isoformat() <= ate)


async def sac_pendencias(conta: str, de: Optional[str] = None, ate: Optional[str] = None) -> dict:
    """Pendências do SAC de uma conta: perguntas sem resposta, mensagens não lidas e reclamações abertas.
    `de`/`ate` (AAAA-MM-DD, horário de Brasília) filtram perguntas e reclamações pela data de criação;
    mensagens não lidas não têm data na API e vêm sempre completas. Nada é marcado como lido."""
    sid = get_seller_id(conta)
    hoje = datetime.now(_BRT).date()
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        q, m = await asyncio.gather(
            _get_json(client, conta, f"/questions/search?seller_id={sid}&status=UNANSWERED&limit=50"
                                     f"&sort_fields=date_created&sort_types=DESC"),
            _get_json(client, conta, "/messages/unread?role=seller&tag=post_sale"))
        claims, off = [], 0
        while off < 400:
            d = await _get_json(client, conta,
                f"/post-purchase/v1/claims/search?status=opened&limit=50&offset={off}&sort=date_created:desc")
            lote = (d or {}).get("data") or []
            claims += lote
            off += len(lote)
            if len(lote) < 50 or off >= ((d or {}).get("paging") or {}).get("total", 0):
                break

    perguntas = []
    for x in (q or {}).get("questions") or []:
        if _no_periodo(x.get("date_created"), de, ate):
            dd = _data_brt(x.get("date_created"))
            perguntas.append({"id": x.get("id"), "data": x.get("date_created"), "mlb": x.get("item_id"),
                              "texto": (x.get("text") or "")[:200], "dias": (hoje - dd).days if dd else None})

    conversas = []
    for r in (m or {}).get("results") or []:
        rec = r.get("resource") or ""
        pack = rec.split("/packs/")[-1].split("/")[0] if "/packs/" in rec else None
        conversas.append({"pack": pack, "nao_lidas": r.get("count", 0), "assistente_ml": "ai-seller" in rec})

    reclamacoes = []
    for c in claims:
        if not _no_periodo(c.get("date_created"), de, ate):
            continue
        vend = next((p for p in c.get("players") or [] if p.get("role") == "respondent"), {})
        acoes = [a.get("action") for a in vend.get("available_actions") or []]
        obrig = [a for a in vend.get("available_actions") or [] if a.get("mandatory")]
        dd = _data_brt(c.get("date_created"))
        reclamacoes.append({"id": c.get("id"), "tipo": c.get("type"), "etapa": c.get("stage"),
                            "motivo": c.get("reason_id"), "pedido": c.get("resource_id"), "data": c.get("date_created"),
                            "dias": (hoje - dd).days if dd else None, "acoes": acoes,
                            "aguarda_voce": bool(acoes), "prazo": next((a.get("due_date") for a in obrig if a.get("due_date")), None)})

    por_tipo = {}
    for r in reclamacoes:
        por_tipo[r["tipo"]] = por_tipo.get(r["tipo"], 0) + 1
    clientes = [c for c in conversas if not c["assistente_ml"]]
    return {
        "success": True, "conta": conta, "de": de, "ate": ate,
        "perguntas": {"total": len(perguntas), "total_api": (q or {}).get("total"), "itens": perguntas,
                      "mais_antiga_dias": max((p["dias"] or 0) for p in perguntas) if perguntas else 0},
        "mensagens": {"nao_lidas": sum(c["nao_lidas"] for c in clientes), "conversas": len(clientes),
                      "assistente_ml": sum(c["nao_lidas"] for c in conversas if c["assistente_ml"]),
                      "itens": conversas, "filtro_de_data_aplicavel": False},
        "reclamacoes": {"total": len(reclamacoes), "aguardam_voce": sum(1 for r in reclamacoes if r["aguarda_voce"]),
                        "por_tipo": por_tipo, "itens": reclamacoes,
                        "mais_antiga_dias": max((r["dias"] or 0) for r in reclamacoes) if reclamacoes else 0},
    }


async def sac_resumo(de: Optional[str] = None, ate: Optional[str] = None, contas=("meli01", "meli02", "meli03")) -> dict:
    """Dashboard geral: pendências das três contas (sem as listas) e totais."""
    res = await asyncio.gather(*[sac_pendencias(c, de, ate) for c in contas], return_exceptions=True)
    saida, tot = {}, {"perguntas": 0, "mensagens": 0, "reclamacoes": 0, "aguardam_voce": 0}
    for c, r in zip(contas, res):
        if isinstance(r, Exception):
            saida[c] = {"erro": f"{type(r).__name__}: {str(r)[:100]}"}
            continue
        saida[c] = {"perguntas": r["perguntas"]["total"], "perguntas_mais_antiga_dias": r["perguntas"]["mais_antiga_dias"],
                    "mensagens": r["mensagens"]["nao_lidas"], "mensagens_assistente_ml": r["mensagens"]["assistente_ml"],
                    "reclamacoes": r["reclamacoes"]["total"], "aguardam_voce": r["reclamacoes"]["aguardam_voce"],
                    "por_tipo": r["reclamacoes"]["por_tipo"], "reclamacoes_mais_antiga_dias": r["reclamacoes"]["mais_antiga_dias"]}
        tot["perguntas"] += saida[c]["perguntas"]; tot["mensagens"] += saida[c]["mensagens"]
        tot["reclamacoes"] += saida[c]["reclamacoes"]; tot["aguardam_voce"] += saida[c]["aguardam_voce"]
    return {"success": True, "de": de, "ate": ate, "contas": saida, "total": tot}
