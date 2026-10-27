"""
marketplaces/amazon/financeiro.py — Finances v0: busca e agregação dos eventos financeiros
MZ App

O Finances registra cada valor na data em que foi POSTADO (envio/estorno), não na data da
compra. Por isso a margem sai daqui e os pedidos servem só para volume e velocidade.

`buscar` faz a rede; `agregar` é matemática pura (testável sem rede).

Sinais da Amazon: cobranças positivas, taxas negativas; nos estornos o contrário
(cobrança devolvida negativa, taxa devolvida positiva). Somar com o sinal dá o líquido.
"""

import time
from collections import defaultdict

from marketplaces.amazon import api

# Campos por SKU. Tudo em R$ (moeda da conta), com o sinal da Amazon.
CAMPOS_SKU = (
    "un_enviadas", "un_devolvidas",
    "receita",            # Principal + frete cobrado + embalagem presente (sem impostos)
    "receita_estornada",  # mesma coisa, nos reembolsos (negativa)
    "promocoes", "promocoes_estornadas",
    "comissao", "comissao_estornada",
    "taxa_fba", "taxa_fba_estornada",
    "outras_taxas", "outras_taxas_estornadas",
)

ESTORNO = {"comissao": "comissao_estornada", "taxa_fba": "taxa_fba_estornada",
           "outras_taxas": "outras_taxas_estornadas"}

# Listas do Finances que viram custo/receita da CONTA (não de um SKU).
LISTAS_TRATADAS = {"ShipmentEventList", "RefundEventList", "ServiceFeeEventList",
                   "AdjustmentEventList", "ProductAdsPaymentEventList"}


def _v(x) -> float:
    return float((x or {}).get("CurrencyAmount") or 0)


def buscar(conta_id: str, inicio_iso: str, fim_iso: str) -> list:
    """
    Todas as páginas de FinancialEvents entre as datas (UTC, ISO 8601).
    Erro em qualquer página aborta: nunca devolve resultado parcial.
    """
    paginas, token = [], None
    while True:
        params = ({"NextToken": token} if token else
                  {"PostedAfter": inicio_iso, "PostedBefore": fim_iso, "MaxResultsPerPage": "100"})
        status, corpo = api.get(conta_id, "/finances/v0/financialEvents", **params)
        if status != 200:
            raise api.falha(status, corpo)
        payload = corpo.get("payload", corpo)
        paginas.append(payload.get("FinancialEvents", {}))
        token = payload.get("NextToken")
        if not token:
            return paginas
        time.sleep(0.6)   # respeita o limite do endpoint


def _tipo_taxa(fee_type: str) -> str:
    if fee_type == "Commission":
        return "comissao"
    if (fee_type or "").startswith("FBA"):
        return "taxa_fba"
    return "outras_taxas"


def _receita(charge_type: str) -> bool:
    """Imposto repassado ao governo não é receita nem custo do vendedor."""
    return "tax" not in (charge_type or "").lower()


def agregar(paginas: list) -> dict:
    """
    {"skus": {sku: {campo: valor}}, "conta": {...}, "nao_tratadas": {lista: qtd}}
    """
    skus = defaultdict(lambda: dict.fromkeys(CAMPOS_SKU, 0.0))
    conta = {"servico": defaultdict(float), "ajustes": defaultdict(float), "ads": 0.0, "ads_faturas": 0}
    nao_tratadas = defaultdict(int)

    for fe in paginas:
        for ev in fe.get("ShipmentEventList", []):
            for it in ev.get("ShipmentItemList", []):
                s = skus[it.get("SellerSKU") or "?"]
                s["un_enviadas"] += it.get("QuantityShipped") or 0
                for c in it.get("ItemChargeList", []):
                    if _receita(c.get("ChargeType")):
                        s["receita"] += _v(c.get("ChargeAmount"))
                for f in it.get("ItemFeeList", []):
                    s[_tipo_taxa(f.get("FeeType"))] += _v(f.get("FeeAmount"))
                for p in it.get("PromotionList", []):
                    s["promocoes"] += _v(p.get("PromotionAmount"))

        for ev in fe.get("RefundEventList", []):
            for it in ev.get("ShipmentItemAdjustmentList", []):
                s = skus[it.get("SellerSKU") or "?"]
                s["un_devolvidas"] += abs(it.get("QuantityShipped") or 0)
                for c in it.get("ItemChargeAdjustmentList", []):
                    if _receita(c.get("ChargeType")):
                        s["receita_estornada"] += _v(c.get("ChargeAmount"))
                for f in it.get("ItemFeeAdjustmentList", []):
                    s[ESTORNO[_tipo_taxa(f.get("FeeType"))]] += _v(f.get("FeeAmount"))
                for p in it.get("PromotionAdjustmentList", []):
                    s["promocoes_estornadas"] += _v(p.get("PromotionAmount"))

        for ev in fe.get("ServiceFeeEventList", []):
            for f in ev.get("FeeList", []):
                conta["servico"][f.get("FeeReason") or f.get("FeeType") or "?"] += _v(f.get("FeeAmount"))
        for ev in fe.get("AdjustmentEventList", []):
            conta["ajustes"][ev.get("AdjustmentType") or "?"] += _v(ev.get("AdjustmentAmount"))
        for ev in fe.get("ProductAdsPaymentEventList", []):
            conta["ads"] += _v(ev.get("transactionValue"))
            conta["ads_faturas"] += 1
        for nome, lista in fe.items():
            if lista and nome not in LISTAS_TRATADAS:
                nao_tratadas[nome] += len(lista)

    conta["servico"], conta["ajustes"] = dict(conta["servico"]), dict(conta["ajustes"])
    return {"skus": {k: dict(v) for k, v in skus.items()}, "conta": conta, "nao_tratadas": dict(nao_tratadas)}


# ─── Agregação por dia (base do painel de rentabilidade) ─────────────────────
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
_LISTAS_COM_DATA = ("ShipmentEventList", "RefundEventList", "ServiceFeeEventList", "AdjustmentEventList",
                    "ProductAdsPaymentEventList")


def _dia(ev: dict, padrao: str = None) -> str:
    """Dia (AAAA-MM-DD, horário de Brasília) em que o evento foi postado."""
    bruto = ev.get("PostedDate") or ev.get("postedDate")
    try:
        return datetime.fromisoformat(bruto.replace("Z", "+00:00")).astimezone(BRT).date().isoformat()
    except Exception:
        return padrao or "sem-data"


def agregar_por_dia(paginas: list, dia_padrao: str = None) -> dict:
    """
    {"skus": {sku: {dia: {campo: valor}}}, "conta": {dia: {"ads": x, "servico": {...}, "ajustes": {...}}}}
    Mesma matemática de `agregar`, repartida pelo dia do evento (sem eventos soltos de lista não tratada).
    """
    skus, conta = {}, {}
    for fe in paginas:
        for lista in _LISTAS_COM_DATA:
            for ev in fe.get(lista, []):
                dia = _dia(ev, dia_padrao)
                parcial = agregar([{lista: [ev]}])
                for sku, campos in parcial["skus"].items():
                    alvo = skus.setdefault(sku, {}).setdefault(dia, dict.fromkeys(CAMPOS_SKU, 0.0))
                    for k, v in campos.items():
                        alvo[k] += v
                c = parcial["conta"]
                if c["ads"] or c["servico"] or c["ajustes"]:
                    d = conta.setdefault(dia, {"ads": 0.0, "servico": {}, "ajustes": {}})
                    d["ads"] += c["ads"]
                    for grupo in ("servico", "ajustes"):
                        for k, v in c[grupo].items():
                            d[grupo][k] = d[grupo].get(k, 0.0) + v
    return {"skus": skus, "conta": conta}


def somar_periodo(por_dia: dict, de: str, ate: str) -> dict:
    """Soma os dias [de, ate] (inclusive) no formato de `agregar`: {"skus": {sku: campos}, "conta": {...}, "nao_tratadas": {}}."""
    skus = {}
    for sku, dias in por_dia["skus"].items():
        tot = dict.fromkeys(CAMPOS_SKU, 0.0)
        achou = False
        for dia, campos in dias.items():
            if de <= dia <= ate:
                achou = True
                for k, v in campos.items():
                    tot[k] += v
        if achou:
            skus[sku] = tot
    conta = {"servico": {}, "ajustes": {}, "ads": 0.0, "ads_faturas": 0}
    for dia, c in por_dia["conta"].items():
        if de <= dia <= ate:
            conta["ads"] += c["ads"]
            for grupo in ("servico", "ajustes"):
                for k, v in c[grupo].items():
                    conta[grupo][k] = conta[grupo].get(k, 0.0) + v
    return {"skus": skus, "conta": conta, "nao_tratadas": {}}
