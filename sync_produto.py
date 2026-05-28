"""
sync_produto.py — Sync direto de um único MLB
Filosofia: não recalcula nada. Retorna os valores exatos que a API ML entrega.
Apenas custos (cmv, frete, etc.) vêm do produtos.json — são dados do usuário, não da API.
"""

import asyncio
import json
import os
from datetime import datetime, timedelta

import ml_api
import parser_rentabilidade as parser

BASE_DIR      = os.path.dirname(__file__)
PRODUTOS_PATH = os.path.join(BASE_DIR, "produtos.json")


def _load_produtos() -> dict:
    if not os.path.exists(PRODUTOS_PATH):
        return {}
    with open(PRODUTOS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


async def sincronizar_e_retornar(
    conta: str,
    mlb: str,
    date_from: str = None,
    date_to: str = None,
) -> dict:
    """
    Busca dados frescos da API ML para um único MLB.
    Retorna os valores diretamente como a API entrega — sem recalcular.
    Apenas margem é calculada localmente pois usa custos do usuário (cmv, frete, etc.).
    """
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")

    # ── Custos cadastrados pelo usuário (não vêm da API) ──────────────────────
    produtos_db = _load_produtos()
    p = produtos_db.get(mlb, {})
    custos = {
        "cmv_unit":      p.get("cmv_unit", 0.0),
        "imposto_pct":   p.get("imposto_pct", parser.get_aliquota(conta)),
        "comissao_pct":  p.get("comissao_pct", 0.11),
        "frete_unit":    p.get("frete_unit", 0.0),
        "roas_objetivo": p.get("roas_objetivo", 0.0),
        "rebot_unit":    p.get("rebot_unit", 0.0),
    }

    erros = []

    # ── 1-3. ORDERS + VISITAS + ADS em paralelo ───────────────────────────────
    orders_res, visitas_res, ads_res = await asyncio.gather(
        ml_api.orders_produto(conta, mlb, date_from, date_to),
        ml_api.visitas_produto(conta, mlb, date_from, date_to),
        ml_api.metricas_ads(conta, mlb, date_from, date_to),
    )

    # ── Processar ORDERS ──────────────────────────────────────────────────────
    if not orders_res["success"]:
        erros.append(f"orders: {orders_res.get('error')}")
        dados_orders = {}
        periodo_stats = {}
    else:
        dados_orders  = parser.processar_orders(
            orders_res.get("data", {}), date_from, date_to
        )
        periodo_stats = dados_orders.pop("_periodo_stats_", {})

    receita_bruta      = 0.0
    receita_paga       = 0.0
    unidades           = 0
    unidades_pagas     = 0
    qtd_vendas         = 0
    compradores_unicos = periodo_stats.get("compradores_unicos", 0)

    for vals in dados_orders.values():
        receita_bruta  += vals.get("receita", 0.0)
        receita_paga   += vals.get("receita_paga", 0.0)
        unidades       += vals.get("unidades", 0)
        unidades_pagas += vals.get("unidades_pagas", 0)
        qtd_vendas     += vals.get("qtd_vendas", 0)

    # REBOT — aplica apenas nas vendas dentro do período de vigência
    rebot_unit   = p.get("rebot_unit", 0.0)
    rebot_inicio = p.get("rebot_inicio", "")
    rebot_fim    = p.get("rebot_fim", "")
    rebot_total  = 0.0

    if rebot_unit > 0 and rebot_inicio and rebot_fim:
        try:
            from datetime import date
            r_ini = datetime.fromisoformat(rebot_inicio).date() if "-" in rebot_inicio else datetime.strptime(rebot_inicio, "%d/%m/%Y").date()
            r_fim = datetime.fromisoformat(rebot_fim).date()    if "-" in rebot_fim    else datetime.strptime(rebot_fim,    "%d/%m/%Y").date()
            p_ini = datetime.fromisoformat(date_from).date()
            p_fim = datetime.fromisoformat(date_to).date()

            inter_ini = max(r_ini, p_ini)
            inter_fim = min(r_fim, p_fim)

            if inter_fim >= inter_ini:
                unidades_rebot = 0
                for dia, vals in dados_orders.items():
                    d = datetime.fromisoformat(dia).date()
                    if inter_ini <= d <= inter_fim:
                        unidades_rebot += vals.get("unidades", 0)
                rebot_total = round(rebot_unit * unidades_rebot, 2)
        except Exception:
            rebot_total = 0.0

    # ── Processar VISITAS ─────────────────────────────────────────────────────
    total_visitas = 0
    if visitas_res["success"]:
        total_visitas = visitas_res["data"]["total_visits"]
    else:
        erros.append(f"visitas: {visitas_res.get('error')}")

    # ── Processar ADS ─────────────────────────────────────────────────────────
    ads           = {}
    is_organic    = False
    catalog_listing = False
    variacoes_count = 0
    if ads_res["success"]:
        ads             = ads_res.get("data", {})
        catalog_listing = ads.get("catalog_listing", False)
        variacoes_count = ads.get("variacoes_count", 0)
    elif ads_res.get("is_organic"):
        is_organic = True
    else:
        erros.append(f"ads: {ads_res.get('error')}")

    ads_total        = ads.get("ads_total", 0.0)
    cliques          = ads.get("cliques", 0)
    impressoes       = ads.get("impressoes", 0)
    cpc              = ads.get("cpc", 0.0)
    ctr              = ads.get("ctr", 0.0)
    receita_publi    = ads.get("receita_publi", 0.0)
    rec_pub_direta   = ads.get("receita_publi_direta", 0.0)
    vendas_publi     = ads.get("vendas_publi", 0)
    vendas_indir     = ads.get("vendas_indiretas", 0)
    vendas_pub_total = ads.get("vendas_publi_total", 0)
    share            = ads.get("share", 0.0)
    advertising_fee  = ads.get("advertising_fee", 0.0)
    roas_target      = ads.get("roas_target", 0.0)
    acos_target      = ads.get("acos_target", 0.0)

    vendas_com_ads = vendas_publi + vendas_indir
    base_sem_ads   = unidades_pagas if unidades_pagas > 0 else unidades
    vendas_sem_ads = max(0, base_sem_ads - vendas_pub_total)

    # ROAS, ACOS, TACOS: API pode retornar None — calculamos igual ao ML faz internamente
    acos_api = ads.get("acos")
    roas_api = ads.get("roas")
    tacos_api = ads.get("tacos")

    roas = round(receita_publi / ads_total, 6) if ads_total > 0 and receita_publi > 0 else (roas_api or 0.0)
    acos = round(ads_total / receita_publi, 6) if ads_total > 0 and receita_publi > 0 else (acos_api or 0.0)

    # TACOS = ads_total / receita_bruta (igual ao ML Seller Center "custo / receita total")
    # Campo tacos da API retorna valores inconsistentes — sempre calculamos localmente
    tacos_decimal = round(ads_total / receita_bruta, 6) if receita_bruta > 0 else 0.0

    # Conversão: qtd_vendas / visitas (API não retorna diretamente)
    conversao = round(qtd_vendas / total_visitas, 6) if total_visitas > 0 else 0.0

    # ── 4. MARGEM — único cálculo local (usa custos do usuário) ──────────────
    margem = parser.calcular_margem(
        receita      = receita_bruta,
        cmv_unit     = custos["cmv_unit"],
        unidades     = unidades,
        imposto_pct  = custos["imposto_pct"],
        comissao_pct = custos["comissao_pct"],
        frete_unit   = custos["frete_unit"],
        ads_total    = ads_total,
        rebot_unit   = 0,
    )
    # Rebot já calculado com interseção de período — ajusta margem manualmente
    margem["margem_total"] = round(margem["margem_total"] + rebot_total, 2)
    margem["rebot_total"]  = rebot_total
    margem["margem_pct"]   = round(margem["margem_total"] / receita_bruta, 6) if receita_bruta > 0 else 0.0

    dias_periodo = (
        datetime.fromisoformat(date_to) - datetime.fromisoformat(date_from)
    ).days + 1

    # Persiste catalog_listing e variacoes_count no produtos.json
    if catalog_listing:
        produtos_db_fresh = _load_produtos()
        if mlb in produtos_db_fresh:
            produtos_db_fresh[mlb]["catalog_listing"] = catalog_listing
            produtos_db_fresh[mlb]["variacoes_count"] = variacoes_count
            import parser_rentabilidade as _parser
            _parser.save_produtos(produtos_db_fresh)

    return {
        "success":        True,
        "mlb":            mlb,
        "conta":          conta,
        "titulo":         p.get("titulo", mlb),
        "sku":            p.get("sku", ""),
        "variacao":       p.get("variacao", ""),
        "is_organic":     is_organic,
        "catalog_listing":  catalog_listing,
        "variacoes_count":  variacoes_count,
        "periodo":    {"de": date_from, "ate": date_to},
        "custos":     custos,
        "metricas": {
            # Vendas — orders API
            "receita":            round(receita_bruta, 2),
            "receita_paga":       round(receita_paga, 2),
            "unidades":           unidades,
            "unidades_pagas":     unidades_pagas,
            "qtd_vendas":         qtd_vendas,
            "compradores_unicos": compradores_unicos,
            # Visitas — visits API
            "visitas":            total_visitas,
            "conversao":          conversao,
            # ADS — campaigns metrics API (valores diretos)
            "ads_total":          round(ads_total, 2),
            "cliques":            cliques,
            "impressoes":         impressoes,
            "cpc_medio":          cpc,
            "ctr":                ctr,
            "tacos":              tacos_decimal,
            "acos":               acos,
            "roas":               roas,
            "receita_publi":      round(receita_publi, 2),
            "receita_publi_direta": round(rec_pub_direta, 2),
            "vendas_publi":       vendas_publi,
            "vendas_indiretas":   vendas_indir,
            "vendas_com_ads":     vendas_com_ads,
            "vendas_sem_publi":   vendas_sem_ads,
            "share":              share,
            "advertising_fee":    advertising_fee,
            "roas_target":        roas_target,
            "acos_target":        acos_target,
            # Margem — calculada com custos do usuário
            "cmv_total":          margem["cmv_total"],
            "imposto_total":      margem["imposto_total"],
            "comissao_total":     margem["comissao_total"],
            "frete_total":        margem["frete_total"],
            "rebot_unit":         rebot_unit,
            "rebot_inicio":       rebot_inicio,
            "rebot_fim":          rebot_fim,
            "rebot_total":        rebot_total,
            "margem_total":       margem["margem_total"],
            "margem_pct":         margem["margem_pct"],
            "margem_unit":        margem["margem_unit"],
            # Meta
            "dias_periodo":       dias_periodo,
            "unidades_dia":       round(unidades / dias_periodo, 2) if dias_periodo > 0 else 0.0,
        },
        "erros": erros,
        "sincronizado_em": datetime.now().isoformat(),
    }
