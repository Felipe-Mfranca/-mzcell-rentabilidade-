"""
marketplaces/amazon/painel.py — Dados do painel de rentabilidade Amazon (tabela por SKU + modal)
MZCell Rentabilidade

O Finances da Amazon é lento (limite baixo de chamadas), então o painel trabalha sobre um cache local por conta
(amazon_cache_<conta>.json, no .gitignore): os últimos 62 dias de eventos, já repartidos por dia e SKU.
`iniciar_atualizacao` busca em segundo plano; `painel` e `sku_detalhe` só leem o cache e fazem contas locais,
então trocar de período (7D, 15D, 30D, personalizado) é instantâneo.

Regras: margem como em rentabilidade.py (imposto sobre receita líquida, CMV sobre unidades líquidas); CMV da base
central (cmv_base) ou de amazon_custos.json; Ads por SKU indisponível (falta a Advertising API): a fatura da
conta aparece à parte e nada é rateado. Dia = dia de Brasília; o dia de hoje fica de fora.
"""

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone

from marketplaces.amazon import api, custos_xlsx, financeiro, relatorios, rentabilidade

JANELA_DIAS = 62
BRT = timezone(timedelta(hours=-3))
_estado = {}
_trava = threading.Lock()


def _caminho(conta: str) -> str:
    return os.path.join(api.BASE_DIR, f"amazon_cache_{conta}.json")


def carregar_cache(conta: str):
    try:
        with open(_caminho(conta), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def estado(conta: str) -> dict:
    c = carregar_cache(conta)
    e = dict(_estado.get(conta, {}))
    e["cache_gerado_em"] = (c or {}).get("gerado_em")
    e["cache_de"], e["cache_ate"] = (c or {}).get("de"), (c or {}).get("ate")
    e["rodando"] = bool(e.get("rodando"))
    return e


def _atualizar(conta: str, dias: int):
    try:
        agora = datetime.now(timezone.utc)
        ini, fim = agora - timedelta(days=dias), agora - timedelta(hours=1)
        iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
        _estado[conta].update(etapa="buscando eventos financeiros (pode levar alguns minutos)")
        paginas = financeiro.buscar(conta, iso(ini), iso(fim))
        _estado[conta].update(etapa="agregando por dia")
        por_dia = financeiro.agregar_por_dia(paginas, dia_padrao=fim.astimezone(BRT).date().isoformat())
        _estado[conta].update(etapa="buscando títulos dos SKUs")
        titulos = {}
        try:
            for x in custos_xlsx.skus_da_conta(conta):
                titulos[x["sku"]] = {"titulo": x.get("titulo", ""), "asin": x.get("asin", "")}
        except Exception:
            pass   # sem título o painel funciona mesmo assim
        dados = {"gerado_em": datetime.now(BRT).isoformat(timespec="seconds"),
                 "de": ini.astimezone(BRT).date().isoformat(), "ate": fim.astimezone(BRT).date().isoformat(),
                 "por_dia": por_dia, "titulos": titulos}
        tmp = _caminho(conta) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False)
        os.replace(tmp, _caminho(conta))
        _estado[conta].update(erro=None, etapa="concluído")
    except Exception as e:
        _estado[conta].update(erro=api.mascarar(f"{type(e).__name__}: {e}")[:300], etapa="falhou")
    finally:
        _estado[conta]["rodando"] = False
        _estado[conta]["fim"] = datetime.now(BRT).isoformat(timespec="seconds")


def iniciar_atualizacao(conta: str, dias: int = JANELA_DIAS) -> dict:
    api.conta_cfg(conta)
    with _trava:
        if _estado.get(conta, {}).get("rodando"):
            return estado(conta)
        _estado[conta] = {"rodando": True, "inicio": datetime.now(BRT).isoformat(timespec="seconds"), "etapa": "iniciando", "erro": None}
        threading.Thread(target=_atualizar, args=(conta, dias), daemon=True).start()
    return estado(conta)


def periodo_padrao(dias: int = 30):
    """(de, ate) fechados: ate = ontem em Brasília."""
    ate = datetime.now(BRT).date() - timedelta(days=1)
    return (ate - timedelta(days=dias - 1)).isoformat(), ate.isoformat()


def _anterior(de: str, ate: str):
    d1, d2 = date.fromisoformat(de), date.fromisoformat(ate)
    n = (d2 - d1).days + 1
    pa = d1 - timedelta(days=1)
    return (pa - timedelta(days=n - 1)).isoformat(), pa.isoformat()


def _validar(de, ate):
    try:
        d1, d2 = date.fromisoformat(de), date.fromisoformat(ate)
    except (TypeError, ValueError):
        raise api.ErroAmazon("datas devem estar no formato AAAA-MM-DD", 400)
    if d2 < d1:
        raise api.ErroAmazon("a data final é anterior à inicial", 400)
    if (d2 - d1).days + 1 > JANELA_DIAS:
        raise api.ErroAmazon(f"período maior que a janela do cache ({JANELA_DIAS} dias)", 400)


def _exige_cache(conta: str) -> dict:
    c = carregar_cache(conta)
    if not c:
        raise api.ErroAmazon("Sem dados locais ainda: clique em Atualizar dados (a primeira carga leva alguns minutos).", 409)
    return c


def painel(conta: str, de: str = None, ate: str = None) -> dict:
    """DRE e linhas por SKU do período, mais o mesmo para o período anterior (para as setas)."""
    if not de or not ate:
        de, ate = periodo_padrao(30)
    _validar(de, ate)
    cache = _exige_cache(conta)
    pa_de, pa_ate = _anterior(de, ate)
    res = {}
    for chave, (a, b) in (("atual", (de, ate)), ("anterior", (pa_de, pa_ate))):
        ag = financeiro.somar_periodo(cache["por_dia"], a, b)
        custos = rentabilidade.custos_com_base(conta, ag["skus"].keys())
        res[chave] = rentabilidade.dre(ag, custos)
    linhas = []
    for sku, x in res["atual"]["skus"].items():
        ant = res["anterior"]["skus"].get(sku)
        t = cache.get("titulos", {}).get(sku, {})
        linhas.append({"sku": sku, "titulo": t.get("titulo", ""), "asin": t.get("asin", ""), **x,
                       "receita_liquida_anterior": ant["receita_liquida"] if ant else None,
                       "margem_anterior": ant["margem"] if ant else None})
    linhas.sort(key=lambda r: -r["receita_liquida"])
    return {"conta": conta, "periodo": {"de": de, "ate": ate}, "anterior": {"de": pa_de, "ate": pa_ate},
            "gerado_em": cache["gerado_em"], "cache": {"de": cache["de"], "ate": cache["ate"]},
            "dre": res["atual"]["dre"], "dre_anterior": res["anterior"]["dre"], "skus": linhas}


def _por_unidade(x: dict) -> dict:
    """Médias por unidade líquida de uma linha de margem_sku (valores positivos de custo)."""
    un = x["un_liquidas"]
    if not un or un <= 0:
        return {"un": un, "disponivel": False}
    f = lambda v: round(abs(v or 0) / un, 2)
    return {"un": un, "disponivel": True, "cmv": f(x["cmv"]) if x["cmv"] is not None else None,
            "imposto": f(x["imposto"]) if x["imposto"] is not None else None, "comissao": f(x["comissao"]),
            "taxa_fba": f(x["taxa_fba"]), "outras_taxas": f(x["outras_taxas"]), "receita": round(x["receita_liquida"] / un, 2)}


def sku_detalhe(conta: str, sku: str, de: str = None, ate: str = None) -> dict:
    if not de or not ate:
        de, ate = periodo_padrao(7)
    _validar(de, ate)
    cache = _exige_cache(conta)
    pa_de, pa_ate = _anterior(de, ate)
    d30, a30 = periodo_padrao(30)

    def linha(a, b):
        ag = financeiro.somar_periodo(cache["por_dia"], a, b)
        custos = rentabilidade.custos_com_base(conta, [sku])
        indice = {k.upper(): k for k in ag["skus"]}
        chave = indice.get(sku.upper())
        if chave is None:
            return None
        return rentabilidade.margem_sku(ag["skus"][chave], custos.get(sku))

    custo = rentabilidade.custos_com_base(conta, [sku]).get(sku)
    com_st, sem_st = custos_xlsx.aliquotas_da_conta(conta)
    atual = linha(de, ate)
    l30 = linha(d30, a30)
    t = cache.get("titulos", {}).get(sku, {})
    return {"conta": conta, "sku": sku, "titulo": t.get("titulo", ""), "asin": t.get("asin", ""),
            "periodo": {"de": de, "ate": ate}, "anterior": {"de": pa_de, "ate": pa_ate},
            "faixa_30d": _por_unidade(l30) if l30 else {"disponivel": False},
            "atual": atual, "periodo_anterior": linha(pa_de, pa_ate), "custo": custo,
            "aliquotas": {"com_st": com_st, "sem_st": sem_st}, "gerado_em": cache["gerado_em"]}


# ─── Visitas e conversão (relatório Sales and Traffic, por ASIN e por período) ────────────────────────
# O relatório devolve uma linha por ASIN para o período pedido (não por dia). Cada período leva de 30 a 60 s, então
# é buscado sob demanda (e para o período anterior, das setas) e guardado em amazon_trafego_<conta>.json.
TRAFEGO_MAX_DIAS = 31
TRAFEGO_VALIDADE_H = 12
_trafego_estado = {}


def _arq_trafego(conta: str) -> str:
    return os.path.join(api.BASE_DIR, f"amazon_trafego_{conta}.json")


def _ler_trafego(conta: str) -> dict:
    try:
        with open(_arq_trafego(conta), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"periodos": {}}


def extrair_trafego(relatorio: dict) -> dict:
    """{ASIN: {sessoes, visualizacoes, conversao_pct, buybox_pct, unidades_pedidas, vendas_pedidas}} do JSON do relatório."""
    saida = {}
    for x in relatorio.get("salesAndTrafficByAsin", []):
        t, v = x.get("trafficByAsin") or {}, x.get("salesByAsin") or {}
        saida[x.get("childAsin")] = {
            "sessoes": t.get("sessions", 0), "visualizacoes": t.get("pageViews", 0),
            "conversao_pct": t.get("unitSessionPercentage"), "buybox_pct": t.get("buyBoxPercentage"),
            "unidades_pedidas": v.get("unitsOrdered", 0),
            "vendas_pedidas": ((v.get("orderedProductSales") or {}).get("amount")) or 0.0}
    return saida


def _baixar_trafego(conta: str, de: str, ate: str):
    chave = f"{de}|{ate}"
    try:
        ini = f"{de}T00:00:00Z"
        fim = (date.fromisoformat(ate) + timedelta(days=1)).isoformat() + "T00:00:00Z"
        rid = relatorios.criar(conta, "GET_SALES_AND_TRAFFIC_REPORT", ini, fim,
                               {"asinGranularity": "CHILD", "dateGranularity": "DAY"})
        doc = relatorios.aguardar(conta, rid, intervalo=8)
        dados = extrair_trafego(json.loads(relatorios.baixar_texto(conta, doc)))
        arq = _ler_trafego(conta)
        arq["periodos"][chave] = {"gerado_em": datetime.now(BRT).isoformat(timespec="seconds"), "asins": dados}
        tmp = _arq_trafego(conta) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(arq, f, ensure_ascii=False)
        os.replace(tmp, _arq_trafego(conta))
        _trafego_estado[(conta, chave)] = {"rodando": False, "erro": None}
    except Exception as e:
        _trafego_estado[(conta, chave)] = {"rodando": False, "erro": api.mascarar(f"{type(e).__name__}: {e}")[:200]}


def trafego(conta: str, de: str, ate: str) -> dict:
    """Situação e dados de visitas do período: {disponivel, rodando, erro, asins}."""
    chave = f"{de}|{ate}"
    p = _ler_trafego(conta)["periodos"].get(chave)
    e = _trafego_estado.get((conta, chave), {})
    vencido = False
    if p and ate >= (datetime.now(BRT).date() - timedelta(days=2)).isoformat():   # período recente muda: renova
        vencido = (datetime.now(BRT) - datetime.fromisoformat(p["gerado_em"])).total_seconds() > TRAFEGO_VALIDADE_H * 3600
    return {"disponivel": bool(p) and not vencido, "rodando": bool(e.get("rodando")), "erro": e.get("erro"),
            "gerado_em": (p or {}).get("gerado_em"), "asins": (p or {}).get("asins", {})}


def iniciar_trafego(conta: str, de: str, ate: str) -> dict:
    api.conta_cfg(conta)
    _validar_trafego(de, ate)
    chave = f"{de}|{ate}"
    with _trava:
        atual = trafego(conta, de, ate)
        if atual["disponivel"] or atual["rodando"]:
            return atual
        _trafego_estado[(conta, chave)] = {"rodando": True, "erro": None}
        threading.Thread(target=_baixar_trafego, args=(conta, de, ate), daemon=True).start()
    return trafego(conta, de, ate)


def _validar_trafego(de, ate):
    _validar(de, ate)
    if (date.fromisoformat(ate) - date.fromisoformat(de)).days + 1 > TRAFEGO_MAX_DIAS:
        raise api.ErroAmazon(f"visitas e conversão aceitam períodos de até {TRAFEGO_MAX_DIAS} dias", 400)


def trafego_do_sku(conta: str, asin: str, de: str, ate: str) -> dict:
    """Visitas do ASIN no período e no anterior (cada um pode estar disponível ou não)."""
    pa_de, pa_ate = _anterior(de, ate)
    out = {}
    for nome, (a, b) in (("atual", (de, ate)), ("anterior", (pa_de, pa_ate))):
        t = trafego(conta, a, b)
        out[nome] = {"disponivel": t["disponivel"], "rodando": t["rodando"], "erro": t["erro"],
                     "dados": t["asins"].get(asin) if t["disponivel"] else None}
    return out
