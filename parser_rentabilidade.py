"""
parser_rentabilidade.py — Processa dados da API ML e calcula rentabilidade
MZCell Rentabilidade
"""

import json
import os
from datetime import datetime, timedelta
from typing import Optional

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")
PRODUTOS_PATH = os.path.join(os.path.dirname(__file__), "produtos.json")


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def load_produtos() -> dict:
    if not os.path.exists(PRODUTOS_PATH):
        return {}
    with open(PRODUTOS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_produtos(data: dict):
    with open(PRODUTOS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ─── IMPOSTOS ────────────────────────────────────────────────────────────────

def get_aliquota(conta: str, tem_st: bool = True) -> float:
    config = load_config()
    impostos = config.get("impostos", {})
    key = f"{conta}_com_st" if tem_st else f"{conta}_sem_st"
    return impostos.get(key, {}).get("aliquota", 0.10)

def get_comissao(config: dict) -> float:
    return config.get("comissao_ml", 0.11)


# ─── CÁLCULO DE MARGEM ───────────────────────────────────────────────────────

def calcular_margem(
    receita: float,
    cmv_unit: float,
    unidades: int,
    imposto_pct: float,
    comissao_pct: float,
    frete_unit: float,
    ads_total: float,
    rebot_unit: float = 0.0,
) -> dict:
    """
    Margem = Receita - CMV - Imposto - Comissão ML - Frete - ADS + Rebot
    """
    cmv_total      = cmv_unit * unidades
    imposto_total  = receita * imposto_pct
    comissao_total = receita * comissao_pct
    frete_total    = frete_unit * unidades
    rebot_total    = rebot_unit * unidades

    margem_total = receita - cmv_total - imposto_total - comissao_total - frete_total - ads_total + rebot_total
    margem_pct   = round(margem_total / receita, 6) if receita > 0 else 0.0
    margem_unit  = round(margem_total / unidades, 2) if unidades > 0 else 0.0

    return {
        "receita":        round(receita, 2),
        "cmv_total":      round(cmv_total, 2),
        "imposto_total":  round(imposto_total, 2),
        "comissao_total": round(comissao_total, 2),
        "frete_total":    round(frete_total, 2),
        "ads_total":      round(ads_total, 2),
        "rebot_total":    round(rebot_total, 2),
        "margem_total":   round(margem_total, 2),
        "margem_pct":     margem_pct,
        "margem_unit":    margem_unit,
    }


# ─── MÉTRICAS ADS ────────────────────────────────────────────────────────────

def calcular_ads_metrics(receita: float, receita_publi: float, ads_total: float, unidades: int) -> dict:
    """
    TACOS = ads_total / receita
    ACOS  = ads_total / receita_publi
    ROAS  = receita_publi / ads_total
    """
    tacos = round(ads_total / receita, 6) if receita > 0 else 0.0
    acos  = round(ads_total / receita_publi, 6) if receita_publi > 0 else 0.0
    roas  = round(receita_publi / ads_total, 6) if ads_total > 0 else 0.0
    return {
        "tacos": tacos,
        "acos":  acos,
        "roas":  roas,
        "vendas_dia": round(unidades / 7, 2) if unidades > 0 else 0.0,
    }


# ─── CONVERSÃO ───────────────────────────────────────────────────────────────

def calcular_conversao(qtd_vendas: int, visitas: int) -> float:
    """Conversão = vendas totais / visitas"""
    return round(qtd_vendas / visitas, 6) if visitas > 0 else 0.0


# ─── ESTRUTURA DO PRODUTO NO DB ──────────────────────────────────────────────

def montar_produto_db(
    mlb: str,
    titulo: str,
    sku: str,
    variacao: str,
    conta: str,
    dados_dias: dict,  # {"YYYY-MM-DD": {...métricas do dia...}}
    custos: Optional[dict] = None,
) -> dict:
    """
    Monta a estrutura padrão de um produto para o produtos.json
    """
    config = load_config()
    custos = custos or {}

    return {
        "mlb": mlb,
        "titulo": titulo,
        "sku": sku,
        "variacao": variacao,
        "conta": conta,
        "cmv_unit": custos.get("cmv_unit", 0.0),
        "imposto_pct": custos.get("imposto_pct", get_aliquota(conta)),
        "comissao_pct": custos.get("comissao_pct", get_comissao(config)),
        "frete_unit": custos.get("frete_unit", 0.0),
        "roas_objetivo": custos.get("roas_objetivo", 0.0),
        "rebot_unit": custos.get("rebot_unit", 0.0),
        "rebot_inicio": custos.get("rebot_inicio", ""),
        "rebot_fim": custos.get("rebot_fim", ""),
        "dias": dados_dias,
        "atualizado_em": datetime.now().isoformat(),
    }


# ─── PROCESSAR ORDERS DA API ─────────────────────────────────────────────────

def processar_orders(orders_data: dict, date_from: str, date_to: str) -> dict:
    """
    Processa a resposta de /orders/search e agrega por dia.
    Retorna: {
      "YYYY-MM-DD": {
        "receita": float,
        "unidades": int,   # soma de order_items[].quantity
        "qtd_vendas": int,
        "preco_unit": float
      }
    }
    Receita bruta = unit_price × quantity alinhado com "Vendas brutas" do ML Seller Center.
    ML inclui cancelled e partially_refunded no GMV bruto.
    Apenas invalid é excluído (pedidos rejeitados antes de qualquer processamento).
    A data é extraída em GMT-4 via datetime.fromisoformat para evitar erros de
    timezone que [:10] introduz quando o campo usa offsets variáveis.
    """
    from collections import defaultdict
    from datetime import datetime, timezone, timedelta

    ML_TZ = timezone(timedelta(hours=-4))  # ML usa GMT-4 fixo

    # invalid: pedido rejeitado antes de qualquer processamento, não conta no GMV.
    # partially_refunded: o pagamento ocorreu (receita real); entra nas brutas igual ao ML.
    # cancelled: INCLUSO — ML "Vendas brutas" conta pedidos iniciados (GMV bruto).
    STATUS_EXCLUIDOS = {"invalid"}
    # Cancelados entram no GMV mas NÃO em unidades_pagas (usado para cálculo de vendas s/ADS).
    STATUS_NAO_PAGOS  = {"cancelled"}
    # "Vendas concluídas" do ML Seller Center = pedidos efetivamente pagos/entregues.
    STATUS_CONCLUIDOS = {"paid", "delivered", "shipped"}

    por_dia: dict = defaultdict(lambda: {
        "receita": 0.0, "receita_paga": 0.0,
        "unidades": 0, "unidades_pagas": 0,
        "qtd_vendas": 0, "qtd_pedidos_pagos": 0,
    })
    buyers: set = set()

    results = orders_data.get("results", [])
    for order in results:
        status = order.get("status", "")
        if status in STATUS_EXCLUIDOS:
            continue

        # Extrai a data em GMT-4, independente do offset armazenado no campo
        raw_date = order.get("date_created", "")
        try:
            dt = datetime.fromisoformat(raw_date)
            date_str = dt.astimezone(ML_TZ).strftime("%Y-%m-%d")
        except Exception:
            date_str = raw_date[:10]

        if not (date_from <= date_str <= date_to):
            continue

        # pack_splitted: cancelamento técnico interno do ML ao dividir um pack.
        # A receita migra para o order substituto (já contabilizado como paid).
        # Validado em 2 MLBs x 4 cenários — alinhado ao comportamento do Seller Center.
        cd_code = (order.get("cancel_detail") or {}).get("code", "")
        if cd_code in {"pack_splitted"}:
            continue

        is_nao_pago   = status in STATUS_NAO_PAGOS
        is_concluido  = status in STATUS_CONCLUIDOS

        # qtd_vendas = 1 por PEDIDO (não por item/unidade) — igual ao ML Seller Center.
        por_dia[date_str]["qtd_vendas"] += 1
        if is_concluido:
            por_dia[date_str]["qtd_pedidos_pagos"] += 1

        buyer_id = order.get("buyer", {}).get("id")
        if buyer_id:
            buyers.add(buyer_id)

        for item in order.get("order_items", []):
            qty        = item.get("quantity", 0)
            unit_price = item.get("unit_price", 0.0)
            por_dia[date_str]["unidades"] += qty
            por_dia[date_str]["receita"]  += unit_price * qty
            if not is_nao_pago:
                # unidades_pagas: exclui cancelados, usado como denominador no cálculo de vendas s/ADS
                por_dia[date_str]["unidades_pagas"] += qty
            if is_concluido:
                # receita_paga: "Vendas concluídas R$" do ML Seller Center
                por_dia[date_str]["receita_paga"] += unit_price * qty

    for dia in por_dia:
        un  = por_dia[dia]["unidades"]
        rec = por_dia[dia]["receita"]
        por_dia[dia]["preco_unit"] = round(rec / un, 2) if un > 0 else 0.0

    resultado = dict(por_dia)
    # Estatísticas de período (não por dia): compradores únicos só podem ser contados globalmente.
    resultado["_periodo_stats_"] = {"compradores_unicos": len(buyers)}
    return resultado


# ─── AGREGAR PERÍODO ─────────────────────────────────────────────────────────

def agregar_periodo(produto: dict, date_from: str, date_to: str) -> dict:
    """
    Soma todas as métricas diárias dentro do período selecionado.
    ADS vem do campo _ads_ (não distribuído por dia para evitar multiplicação).
    """
    dias = produto.get("dias", {})
    # ADS e visitas são distribuídos por dia no sync (proporcional à receita).
    # A soma diária já é o valor correto para qualquer período filtrado.
    total = {
        "receita": 0.0, "receita_paga": 0.0,
        "ads_total": 0.0, "receita_publi": 0.0,
        "receita_publi_direta": 0.0,
        "impressoes": 0, "cliques": 0, "vendas_publi": 0,
        "vendas_indiretas": 0,
        "visitas": 0, "unidades": 0, "unidades_pagas": 0,
        "qtd_vendas": 0, "qtd_pedidos_pagos": 0,
        "cmv_total": 0.0, "imposto_total": 0.0,
        "comissao_total": 0.0, "frete_total": 0.0, "margem_total": 0.0,
    }

    for dia, vals in dias.items():
        if dia == "_ads_":
            continue  # Apenas metadados; os totais vêm dos dias distribuídos
        if date_from <= dia <= date_to:
            for k in total:
                total[k] += vals.get(k, 0)

    # _ads_ contém metadados de razão/média (cpc, ctr, share) e estatísticas de período
    # (compradores_unicos) que não fazem sentido distribuir por dia.
    ads_data = dias.get("_ads_", {})
    if ads_data:
        total["cpc_medio"]          = ads_data.get("cpc", 0.0)
        total["ctr"]                = ads_data.get("ctr", 0.0)
        total["share"]              = ads_data.get("share", 0.0)
        total["advertising_fee"]    = ads_data.get("advertising_fee", 0.0)
        total["compradores_unicos"] = ads_data.get("compradores_unicos", 0)
        # tacos_campanha: valor oficial do ML (campo `tacos` da API de métricas ADS).
        # Mais preciso que nosso cálculo (ads/receita) pois o ML usa receita própria do período.
        total["tacos_campanha"]     = ads_data.get("tacos_campanha", 0.0)

    # Derivados: para o período exato do sync lê os totais brutos de _ads_ (evita
    # perda de precisão de valores pequenos que arredondam para 0 ao serem distribuídos
    # proporcionalmente por dia e depois re-somados).
    ads_periodo_de  = ads_data.get("_periodo_de", "")
    ads_periodo_ate = ads_data.get("_periodo_ate", "")
    periodo_exato   = (ads_periodo_de == date_from and ads_periodo_ate == date_to
                       and bool(ads_periodo_de))

    if periodo_exato and "vendas_publi" in ads_data:
        total["vendas_publi"]     = ads_data["vendas_publi"]
        total["vendas_indiretas"] = ads_data.get("vendas_indiretas", 0)

    total["vendas_com_ads"] = total["vendas_publi"] + total["vendas_indiretas"]

    if periodo_exato and "vendas_sem_publi" in ads_data:
        total["vendas_sem_publi"] = ads_data["vendas_sem_publi"]
    else:
        # unidades_pagas exclui cancelados — denominador correto para vendas s/ADS
        base = total["unidades_pagas"] if total["unidades_pagas"] > 0 else total["unidades"]
        total["vendas_sem_publi"] = max(0, base - total["vendas_com_ads"])

    dias_periodo = (
        datetime.fromisoformat(date_to) - datetime.fromisoformat(date_from)
    ).days + 1

    total["receita"]      = round(total["receita"], 2)
    total["margem_pct"]   = round(total["margem_total"] / total["receita"], 6) if total["receita"] > 0 else 0.0
    total["margem_unit"]  = round(total["margem_total"] / total["unidades"], 2) if total["unidades"] > 0 else 0.0
    total["tacos"]        = round(total["ads_total"] / total["receita"], 6) if total["receita"] > 0 else 0.0
    total["acos"]         = round(total["ads_total"] / total["receita_publi"], 6) if total["receita_publi"] > 0 else 0.0
    total["roas"]         = round(total["receita_publi"] / total["ads_total"], 6) if total["ads_total"] > 0 else 0.0
    # conversao = qtd_vendas (pedidos, 1 por order) / visitas — igual ao ML Seller Center
    total["conversao"]    = round(total["qtd_vendas"] / total["visitas"], 6) if total["visitas"] > 0 else 0.0
    total["unidades_dia"] = round(total["unidades"] / dias_periodo, 2) if dias_periodo > 0 else 0.0
    total["dias_periodo"] = dias_periodo

    return total
