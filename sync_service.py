"""
sync_service.py — Lógica de sync reutilizável e controle de status
Usado por main.py (rota HTTP + background tasks) e refresh_ml_token.py (schedule diário)
"""

import json
import os
from datetime import datetime, timedelta

import ml_api
import parser_rentabilidade as parser

BASE_DIR         = os.path.dirname(__file__)
PRODUTOS_PATH    = os.path.join(BASE_DIR, "produtos.json")
SYNC_STATUS_PATH = os.path.join(BASE_DIR, "sync_status.json")
LOG_PATH         = os.path.join(BASE_DIR, "log_atividades.json")


def _load(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save(path: str, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_sync_status() -> dict:
    return _load(SYNC_STATUS_PATH) or {}

def update_sync_status(conta: str, **kwargs):
    status = get_sync_status()
    if conta not in status:
        status[conta] = {}
    status[conta].update(kwargs)
    _save(SYNC_STATUS_PATH, status)

def _log(msg: str):
    logs = _load(LOG_PATH) or []
    logs.append({"ts": datetime.now().isoformat(), "msg": msg})
    _save(LOG_PATH, logs[-500:])


async def _sync_mlb(conta: str, mlb: str, detalhe: dict,
                    date_from: str, date_to: str, produtos_db: dict) -> bool:
    """Sync de um único produto — busca orders, visitas e ADS e salva em produtos_db.
    Retorna True se atualizado com sucesso."""
    titulo   = detalhe.get("title", mlb)
    sku      = detalhe.get("seller_custom_field", "") or ""
    variacao = ""
    for attr in detalhe.get("attributes", []):
        if attr.get("id") == "COLOR":
            variacao = attr.get("value_name", "")
            break

    orders_res = await ml_api.orders_produto(conta, mlb, date_from, date_to)
    dados_dias = parser.processar_orders(
        orders_res.get("data", {}), date_from, date_to
    ) if orders_res["success"] else {}

    periodo_stats = dados_dias.pop("_periodo_stats_", {})

    visitas_res   = await ml_api.visitas_produto(conta, mlb, date_from, date_to)
    total_visitas = visitas_res["data"]["total_visits"] if visitas_res["success"] else 0

    vis_dia_res = await ml_api.visitas_diarias(conta, mlb, date_from, date_to)
    vis_por_dia = vis_dia_res.get("data", {}) if vis_dia_res.get("success") else {}

    ads_res              = await ml_api.metricas_ads(conta, mlb, date_from, date_to)
    total_receita_period = sum(v.get("receita", 0.0) for k, v in dados_dias.items() if k != "_ads_")

    if ads_res["success"]:
        ads_data           = ads_res.get("data", {})
        total_ads          = ads_data.get("ads_total", 0.0)
        total_cliques      = ads_data.get("cliques", 0)
        total_impressoes   = ads_data.get("impressoes", 0)
        total_rec_publi    = ads_data.get("receita_publi", 0.0)
        total_rec_pub_dir  = ads_data.get("receita_publi_direta", 0.0)
        total_vend_pub     = ads_data.get("vendas_publi", 0)
        total_vend_ind     = ads_data.get("vendas_indiretas", 0)
        total_vend_pub_tot = ads_data.get("vendas_publi_total", 0)
        total_unid_pagas   = sum(v.get("unidades_pagas", 0) for v in dados_dias.values())

        for dia, vals in dados_dias.items():
            peso = vals.get("receita", 0.0) / total_receita_period if total_receita_period > 0 else 0.0
            vals["ads_total"]            = round(total_ads * peso, 4)
            vals["cliques"]              = round(total_cliques * peso)
            vals["impressoes"]           = round(total_impressoes * peso)
            vals["receita_publi"]        = round(total_rec_publi * peso, 4)
            vals["receita_publi_direta"] = round(total_rec_pub_dir * peso, 4)
            vals["vendas_publi"]         = round(total_vend_pub * peso)
            vals["vendas_indiretas"]     = round(total_vend_ind * peso)
            vals["visitas"]              = vis_por_dia.get(dia, round(total_visitas * peso))

        dados_dias["_ads_"] = {
            "ads_total":            round(total_ads, 2),
            "tacos_campanha":       ads_data.get("tacos", 0.0) / 100,
            "impressoes":           total_impressoes,
            "cliques":              total_cliques,
            "receita_publi":        round(total_rec_publi, 2),
            "receita_publi_direta": round(total_rec_pub_dir, 2),
            "vendas_publi":         total_vend_pub,
            "vendas_indiretas":     total_vend_ind,
            "vendas_sem_publi":     max(0, total_unid_pagas - total_vend_pub_tot),
            "share":                ads_data.get("share", 0.0),
            "advertising_fee":      ads_data.get("advertising_fee", 0.0),
            "cpc":                  ads_data.get("cpc", 0.0),
            "ctr":                  ads_data.get("ctr", 0.0),
            "visitas":              total_visitas,
            "compradores_unicos":   periodo_stats.get("compradores_unicos", 0),
            "_periodo_de":          date_from,
            "_periodo_ate":         date_to,
        }
    else:
        for dia, vals in dados_dias.items():
            peso = vals.get("receita", 0.0) / total_receita_period if total_receita_period > 0 else 0.0
            vals["ads_total"] = 0.0
            vals["visitas"]   = vis_por_dia.get(dia, round(total_visitas * peso))
        dados_dias["_ads_"] = {
            "ads_total": 0.0, "tacos_campanha": 0.0,
            "visitas": total_visitas,
            "compradores_unicos": periodo_stats.get("compradores_unicos", 0),
            "_periodo_de": date_from, "_periodo_ate": date_to,
        }

    custos_existentes = {}
    if mlb in produtos_db:
        p = produtos_db[mlb]
        custos_existentes = {
            "cmv_unit":    p.get("cmv_unit", 0.0),
            "imposto_pct": p.get("imposto_pct", parser.get_aliquota(conta)),
            "comissao_pct": p.get("comissao_pct", 0.11),
            "frete_unit":  p.get("frete_unit", 0.0),
            "rebot_unit":  p.get("rebot_unit", 0.0),
        }

    for dia, vals in dados_dias.items():
        if dia == "_ads_":
            continue
        margem = parser.calcular_margem(
            receita      = vals.get("receita", 0),
            cmv_unit     = custos_existentes.get("cmv_unit", 0),
            unidades     = vals.get("unidades", 0),
            imposto_pct  = custos_existentes.get("imposto_pct", 0.10),
            comissao_pct = custos_existentes.get("comissao_pct", 0.11),
            frete_unit   = custos_existentes.get("frete_unit", 0),
            ads_total    = vals.get("ads_total", 0),
            rebot_unit   = custos_existentes.get("rebot_unit", 0),
        )
        dados_dias[dia].update(margem)

    if mlb in produtos_db:
        produtos_db[mlb]["dias"]          = dados_dias
        produtos_db[mlb]["titulo"]        = titulo
        produtos_db[mlb]["atualizado_em"] = datetime.now().isoformat()
    else:
        produtos_db[mlb] = parser.montar_produto_db(
            mlb=mlb, titulo=titulo, sku=sku, variacao=variacao,
            conta=conta, dados_dias=dados_dias, custos=custos_existentes
        )
    return True


async def sincronizar_produto_func(conta: str, mlb: str,
                                    date_from: str = None, date_to: str = None) -> dict:
    """Sync de um único produto — busca detalhe, orders, visitas e ADS."""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")

    detalhes = await ml_api.detalhes_multiplos(conta, [mlb])
    if not detalhes["success"]:
        return {"success": False, "error": detalhes.get("error", "Erro ao buscar detalhes")}

    detalhe     = detalhes["data"].get(mlb, {})
    produtos_db = _load(PRODUTOS_PATH) or {}

    ok = await _sync_mlb(conta, mlb, detalhe, date_from, date_to, produtos_db)
    if not ok:
        return {"success": False, "error": f"Falha ao sincronizar {mlb}"}

    _save(PRODUTOS_PATH, produtos_db)
    ts = datetime.now().isoformat()
    _log(f"Sync individual {mlb} ({conta}): {date_from} a {date_to}")
    return {
        "success":    True,
        "conta":      conta,
        "mlb":        mlb,
        "ultimo_sync": ts,
        "periodo":    {"de": date_from, "ate": date_to},
    }


async def sincronizar_conta_func(conta: str, dias: int = 30,
                                  date_from: str = None, date_to: str = None) -> dict:
    """Sync completo: orders + visitas + ADS de todos os produtos ativos da conta."""
    if not date_to:
        date_to = datetime.now().strftime("%Y-%m-%d")
    if not date_from:
        date_from = (datetime.now() - timedelta(days=dias - 1)).strftime("%Y-%m-%d")

    res = await ml_api.listar_produtos(conta)
    if not res["success"]:
        return {"success": False, "error": res["error"]}

    mlbs = res["produtos"]
    if not mlbs:
        return {"success": True, "msg": "Nenhum produto ativo", "total": 0}

    detalhes    = await ml_api.detalhes_multiplos(conta, mlbs)
    produtos_db = _load(PRODUTOS_PATH) or {}
    atualizados = 0

    for mlb in mlbs:
        detalhe = detalhes["data"].get(mlb, {})
        ok = await _sync_mlb(conta, mlb, detalhe, date_from, date_to, produtos_db)
        if ok:
            atualizados += 1

    _save(PRODUTOS_PATH, produtos_db)
    ts = datetime.now().isoformat()
    update_sync_status(conta,
        ultimo_sync=ts,
        produtos_atualizados=atualizados,
        periodo_de=date_from,
        periodo_ate=date_to,
    )
    _log(f"Sync {conta}: {atualizados} produtos ({date_from} a {date_to})")
    return {
        "success":           True,
        "conta":             conta,
        "total_atualizados": atualizados,
        "ultimo_sync":       ts,
        "periodo":           {"de": date_from, "ate": date_to},
    }
