"""
sync_service.py — Lógica de sync reutilizável e controle de status
Usado por main.py (rota HTTP + background tasks) e refresh_ml_token.py (schedule diário)
"""

import asyncio
import copy
import json
import os
from datetime import datetime, timedelta

import httpx

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


async def _custos_vigentes(conta: str, mlb: str, detalhe: dict) -> dict:
    """Preço vigente (com promoção), frete do ML nesse preço, comissão do ML e rebote da campanha ativa.
    Tudo vem do ML a cada sincronização. Campos que o ML não devolver ficam None."""
    out = {"preco": None, "frete": None, "comissao_pct": None, "rebot_unit": None}
    try:
        async with httpx.AsyncClient(timeout=30, verify=False) as client:
            sp = await ml_api._get_json(client, conta, f"/items/{mlb}/sale_price?context=channel_marketplace")
            promos = await ml_api._get_json(client, conta, f"/seller-promotions/items/{mlb}?app_version=v2")
    except Exception:
        sp, promos = None, None
    vigente = (sp or {}).get("amount") or detalhe.get("price")
    if not vigente:
        return out
    vigente = float(vigente)
    out["preco"] = vigente
    frete = await ml_api.frete_para_preco(conta, mlb, vigente)
    if frete.get("success"):
        out["frete"] = frete.get("frete")
    lt, cat = detalhe.get("listing_type_id"), detalhe.get("category_id")
    if lt and cat:
        try:
            async with httpx.AsyncClient(timeout=30, verify=False) as client:
                lp = await ml_api._get_json(client, conta,
                    f"/sites/MLB/listing_prices?price={vigente}&listing_type_id={lt}&category_id={cat}")
            row = (lp[0] if isinstance(lp, list) and lp else lp) or {}
            fee = (row.get("sale_fee_details") or {}).get("percentage_fee")
            if fee is not None:
                out["comissao_pct"] = round(float(fee) / 100, 6)
        except Exception:
            pass
    if isinstance(promos, list):
        ativa = ml_api.campanha_efetiva(promos, sp)
        cheio = (sp or {}).get("regular_amount") or detalhe.get("price") or vigente
        if ativa:
            out["rebot_unit"] = round(((ativa.get("meli_percentage") or 0) / 100) * (ativa.get("original_price") or cheio), 2)
        else:
            out["rebot_unit"] = 0.0
    return out


async def _sync_mlb(conta: str, mlb: str, detalhe: dict,
                    date_from: str, date_to: str, produtos_db: dict) -> bool:
    """Sync de um único produto — busca orders, visitas e ADS e salva em produtos_db.
    Retorna True se atualizado com sucesso."""
    titulo   = detalhe.get("title", mlb)
    sku      = ml_api.sku_do_anuncio(detalhe)
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

    vig = await _custos_vigentes(conta, mlb, detalhe)
    preco_vigente, frete_vigente = vig["preco"], vig["frete"]

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

    # frete, comissão e rebote são sempre os do ML no momento da sincronização (nunca dados antigos)
    if frete_vigente is not None:
        custos_existentes["frete_unit"] = frete_vigente
    if vig["comissao_pct"] is not None:
        custos_existentes["comissao_pct"] = vig["comissao_pct"]
    if vig["rebot_unit"] is not None:
        custos_existentes["rebot_unit"] = vig["rebot_unit"]
    # CMV: a base de CMV (importada) pelo SKU é a referência; sobrescreve o valor antigo do produto
    import cmv_base
    cmv_ref = cmv_base.cmv_de(sku)
    if cmv_ref is not None:
        custos_existentes["cmv_unit"] = cmv_ref

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
        # mantém os dias anteriores ao período (histórico dos modais), até 130 dias antes do fim do período
        corte = (datetime.fromisoformat(date_to) - timedelta(days=130)).strftime("%Y-%m-%d")
        antigos = {d: v for d, v in (produtos_db[mlb].get("dias") or {}).items()
                   if d != "_ads_" and corte <= d < date_from}
        produtos_db[mlb]["dias"]          = {**antigos, **dados_dias}
        produtos_db[mlb]["titulo"]        = titulo
        produtos_db[mlb]["atualizado_em"] = datetime.now().isoformat()
        if frete_vigente is not None:
            produtos_db[mlb]["frete_unit"] = frete_vigente
        if vig["comissao_pct"] is not None:
            produtos_db[mlb]["comissao_pct"] = vig["comissao_pct"]
        if vig["rebot_unit"] is not None:
            produtos_db[mlb]["rebot_unit"] = vig["rebot_unit"]
        if cmv_ref is not None:
            produtos_db[mlb]["cmv_unit"] = cmv_ref
    else:
        produtos_db[mlb] = parser.montar_produto_db(
            mlb=mlb, titulo=titulo, sku=sku, variacao=variacao,
            conta=conta, dados_dias=dados_dias, custos=custos_existentes
        )
    # ROAS objetivo vigente da campanha no ML; anúncio orgânico (sem campanha) fica sem objetivo
    if ads_res["success"]:
        produtos_db[mlb]["roas_objetivo_ml"] = ads_res["data"].get("roas_target") or None
    elif ads_res.get("error") == "organico":
        produtos_db[mlb]["roas_objetivo_ml"] = None
    if preco_vigente is not None:
        produtos_db[mlb]["preco_vigente"] = preco_vigente
    # vínculo de catálogo: o anúncio vencedor do catálogo e se este é ele (só quando o detalhe traz o catálogo)
    cpid = detalhe.get("catalog_product_id")
    if cpid:
        try:
            produtos_db[mlb]["catalogo"] = await ml_api.vinculo_catalogo(conta, cpid, mlb)
        except Exception:
            pass
    elif detalhe and "catalog_product_id" in detalhe:
        produtos_db[mlb]["catalogo"] = None      # detalhe completo sem catálogo: desfaz vínculo antigo
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

    falhas = []
    for mlb in mlbs:
        detalhe = detalhes["data"].get(mlb, {})
        try:                       # um anúncio com erro de rede não derruba a sincronização da conta inteira
            ok = await _sync_mlb(conta, mlb, detalhe, date_from, date_to, produtos_db)
        except Exception as e:
            ok = False
            falhas.append(mlb)
            _log(f"Falha ao sincronizar {mlb} ({conta}): {type(e).__name__}")
        if ok:
            atualizados += 1

    # anúncios da conta que não estão mais ativos no ML: não entram na validade (não há o que sincronizar)
    ativos = set(mlbs)
    for m, p in produtos_db.items():
        if p.get("conta") == conta:
            p["inativo_no_ml"] = m not in ativos

    _save(PRODUTOS_PATH, produtos_db)
    ts = datetime.now().isoformat()
    update_sync_status(conta,
        ultimo_sync=ts,
        produtos_atualizados=atualizados,
        periodo_de=date_from,
        periodo_ate=date_to,
    )
    _log(f"Sync {conta}: {atualizados} produtos ({date_from} a {date_to})")
    try:                       # histórico de até 120 dias, em segundo plano, sem atrasar a resposta
        asyncio.create_task(sincronizar_historico(conta, date_from, date_to))
    except RuntimeError:
        pass
    return {
        "success":           True,
        "conta":             conta,
        "total_atualizados": atualizados,
        "falhas":            falhas,
        "ultimo_sync":       ts,
        "periodo":           {"de": date_from, "ate": date_to},
    }


# ─── Histórico em segundo plano (alimenta os períodos P3 e P4 dos modais) ─────────────────────
HISTORICO_DIAS = 120
_historicos_rodando: set = set()


async def sincronizar_historico(conta: str, date_from: str, date_to: str, dias_total: int = HISTORICO_DIAS):
    """Completa os dias anteriores ao último sync, até `dias_total` dias contados de date_to, em blocos de 30 dias.
    Cada bloco tem o ADS dividido só dentro dele (mais fiel que dividir 120 dias de uma vez) e só entram dias
    que o produto ainda não tem: nunca mexe no período recém-sincronizado. Roda no máximo uma vez por dia e conta."""
    hoje = datetime.now().strftime("%Y-%m-%d")
    st = get_sync_status().get(conta, {})
    if conta in _historicos_rodando or st.get("historico_em", "")[:10] == hoje and st.get("historico_ate") == date_to:
        return
    _historicos_rodando.add(conta)
    update_sync_status(conta, historico_rodando=True)
    try:
        limite = (datetime.fromisoformat(date_to) - timedelta(days=dias_total - 1)).strftime("%Y-%m-%d")
        blocos, ate = [], datetime.fromisoformat(date_from) - timedelta(days=1)
        while ate.strftime("%Y-%m-%d") >= limite:
            de = max(ate - timedelta(days=29), datetime.fromisoformat(limite))
            blocos.append((de.strftime("%Y-%m-%d"), ate.strftime("%Y-%m-%d")))
            ate = de - timedelta(days=1)
        base = _load(PRODUTOS_PATH) or {}
        mlbs = [m for m, p in base.items() if p.get("conta") == conta]
        coletado = {}                                   # mlb -> {dia: valores}
        for mlb in mlbs:
            for de, ate_b in blocos:
                tmp = {mlb: copy.deepcopy(base[mlb])}
                try:
                    if await _sync_mlb(conta, mlb, {"title": base[mlb].get("titulo", mlb)}, de, ate_b, tmp):
                        for dia, v in (tmp[mlb].get("dias") or {}).items():
                            if dia != "_ads_" and de <= dia <= ate_b:
                                coletado.setdefault(mlb, {})[dia] = v
                except Exception:
                    pass
                await asyncio.sleep(0.2)
        fresh = _load(PRODUTOS_PATH) or {}               # relê: o usuário pode ter editado custos durante o processo
        novos = 0
        for mlb, dias in coletado.items():
            if mlb in fresh:
                d = fresh[mlb].setdefault("dias", {})
                for dia, v in dias.items():
                    if dia not in d:
                        d[dia] = v
                        novos += 1
        _save(PRODUTOS_PATH, fresh)
        update_sync_status(conta, historico_em=datetime.now().isoformat(), historico_ate=date_to,
                           historico_de=limite, historico_dias_novos=novos)
        _log(f"Histórico {conta}: {novos} dias de produto adicionados ({limite} a {date_from})")
    except Exception as e:
        _log(f"Histórico {conta} falhou: {type(e).__name__}: {str(e)[:150]}")
    finally:
        _historicos_rodando.discard(conta)
        update_sync_status(conta, historico_rodando=False)
