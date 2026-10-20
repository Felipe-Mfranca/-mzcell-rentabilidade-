"""
marketplaces/amazon/rentabilidade.py — Margem por SKU e DRE da conta (Amazon)
MZCell Rentabilidade

Uso:  .\\venv\\Scripts\\python.exe -m marketplaces.amazon.rentabilidade amazon02 [--dias 30]

Regras:
  - CMV e imposto são POR SKU, em amazon_custos.json (gitignored; modelo em
    amazon_custos.exemplo.json):  {"amazon02": {"<SKU>": {"cmv_unit": 20.0, "imposto_pct": 0.10}}}
  - Custos da conta (Ads, armazenagem, assinatura, remoção...) NÃO são rateados por SKU: ficam
    abaixo da margem de contribuição, na DRE.
  - Imposto = imposto_pct × receita líquida (receita − estornos − promoções).
  - CMV = cmv_unit × unidades líquidas (enviadas − devolvidas); supõe que a devolução volta ao
    estoque vendável.
  - SKU sem custo cadastrado mostra a margem "após taxas" e fica marcado como incompleto.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

from marketplaces.amazon import api, financeiro

CUSTOS_PATH = os.path.join(api.BASE_DIR, "amazon_custos.json")


def carregar_custos(conta_id: str) -> dict:
    try:
        with open(CUSTOS_PATH, encoding="utf-8") as f:
            return json.load(f).get(conta_id, {})
    except FileNotFoundError:
        return {}
    except ValueError as e:
        raise api.ErroAmazon(f"amazon_custos.json inválido: {e}", 500)


def custos_com_base(conta_id: str, skus) -> dict:
    """Custos por SKU: o que está em amazon_custos.json (tem prioridade) e, para os demais, o CMV da base central
    (cmv_base.json, um arquivo para todas as plataformas) com a alíquota COM ST da conta. SKU sem CMV em nenhum
    dos dois fica sem custo (margem incompleta)."""
    custos = dict(carregar_custos(conta_id))
    indice = {k.upper(): k for k in custos}
    try:
        import cmv_base
    except ImportError:
        return custos
    base = cmv_base.carregar()
    com_st, _ = cmv_base.aliquotas_da_conta(conta_id)
    for sku in skus:
        if sku.upper() in indice:
            custos.setdefault(sku, custos[indice[sku.upper()]])
            continue
        cmv = cmv_base.cmv_de(sku, base)
        if cmv is not None:
            custos[sku] = {"cmv_unit": cmv, "imposto_pct": com_st, "tem_st": True, "origem": "base"}
    return custos


def margem_sku(a: dict, custo: dict = None) -> dict:
    """a = agregado do SKU (financeiro.agregar). custo = {"cmv_unit":..., "imposto_pct":...} ou None."""
    receita_liquida = a["receita"] + a["receita_estornada"] + a["promocoes"] + a["promocoes_estornadas"]
    comissao = a["comissao"] + a["comissao_estornada"]
    fba = a["taxa_fba"] + a["taxa_fba_estornada"]
    outras = a["outras_taxas"] + a["outras_taxas_estornadas"]
    apos_taxas = receita_liquida + comissao + fba + outras
    un_liq = a["un_enviadas"] - a["un_devolvidas"]

    r = {
        "un_enviadas": a["un_enviadas"], "un_devolvidas": a["un_devolvidas"], "un_liquidas": un_liq,
        "receita": a["receita"], "estornos": a["receita_estornada"],
        "promocoes": a["promocoes"] + a["promocoes_estornadas"],
        "receita_liquida": receita_liquida,
        "comissao": comissao, "taxa_fba": fba, "outras_taxas": outras,
        "margem_apos_taxas": apos_taxas,
        "imposto": None, "cmv": None, "margem": None, "margem_pct": None, "completo": False,
    }
    if custo and custo.get("cmv_unit") is not None and custo.get("imposto_pct") is not None:
        r["imposto"] = -round(max(receita_liquida, 0) * custo["imposto_pct"], 2)
        r["cmv"] = -round(custo["cmv_unit"] * un_liq, 2)
        r["margem"] = round(apos_taxas + r["imposto"] + r["cmv"], 2)
        r["margem_pct"] = round(r["margem"] / receita_liquida * 100, 2) if receita_liquida > 0 else None
        r["completo"] = True
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in r.items()}


def dre(agregado: dict, custos: dict) -> dict:
    """DRE da conta: soma dos SKUs (margem de contribuição) e, abaixo, os custos da conta."""
    linhas = {sku: margem_sku(a, custos.get(sku)) for sku, a in agregado["skus"].items()}
    soma = lambda campo: round(sum(x[campo] or 0 for x in linhas.values()), 2)
    completos = [x for x in linhas.values() if x["completo"]]
    d = {
        "receita": soma("receita"), "estornos": soma("estornos"), "promocoes": soma("promocoes"),
        "receita_liquida": soma("receita_liquida"),
        "comissao": soma("comissao"), "taxa_fba": soma("taxa_fba"), "outras_taxas": soma("outras_taxas"),
        "margem_apos_taxas": soma("margem_apos_taxas"),
        "imposto": soma("imposto"), "cmv": soma("cmv"),
        "skus_total": len(linhas), "skus_com_custo": len(completos),
        "margem_contribuicao": round(sum(x["margem"] for x in completos), 2),
    }
    conta = agregado["conta"]
    d["conta"] = {
        "ads": round(conta["ads"], 2),
        "servicos": {k: round(v, 2) for k, v in conta["servico"].items()},
        "ajustes": {k: round(v, 2) for k, v in conta["ajustes"].items()},
    }
    custos_conta = conta["ads"] + sum(conta["servico"].values()) + sum(conta["ajustes"].values())
    d["conta"]["total"] = round(custos_conta, 2)
    d["resultado"] = round(d["margem_contribuicao"] + custos_conta, 2) if len(completos) == len(linhas) else None
    d["nao_tratadas"] = agregado["nao_tratadas"]
    return {"dre": d, "skus": linhas}


def apurar(conta_id: str, dias: int = 30) -> dict:
    agora = datetime.now(timezone.utc)
    ini, fim = agora - timedelta(days=dias), agora - timedelta(hours=1)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    agregado = financeiro.agregar(financeiro.buscar(conta_id, iso(ini), iso(fim)))
    r = dre(agregado, custos_com_base(conta_id, agregado["skus"].keys()))
    r.update(conta=conta_id, periodo={"de": iso(ini), "ate": iso(fim)}, dias=dias)
    return r


def _brl(v) -> str:
    return "      —" if v is None else f"{v:>10,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatar(r: dict) -> str:
    d = r["dre"]
    L = [f"DRE Amazon — {r['conta']} — últimos {r['dias']} dias (por data de lançamento no Finances)", ""]
    for rot, k in (("Receita bruta", "receita"), ("(-) Reembolsos", "estornos"), ("(-) Promoções", "promocoes"),
                   ("= Receita líquida", "receita_liquida"), ("(-) Comissão", "comissao"), ("(-) Taxa FBA", "taxa_fba"),
                   ("(-) Outras taxas por item", "outras_taxas"), ("= Margem após taxas Amazon", "margem_apos_taxas"),
                   ("(-) Imposto", "imposto"), ("(-) CMV", "cmv")):
        L.append(f"  {rot:<30}{_brl(d[k])}")
    L.append(f"  {'= Margem de contribuição':<30}{_brl(d['margem_contribuicao'])}   "
             f"({d['skus_com_custo']} de {d['skus_total']} SKUs com custo)")
    L += ["", "  Custos da conta (não rateados):", f"  {'(-) Ads (faturas)':<30}{_brl(d['conta']['ads'])}"]
    L += [f"  {'(-) ' + k:<30}{_brl(v)}" for k, v in d["conta"]["servicos"].items()]
    L += [f"  {'(+/-) ajuste ' + k:<30}{_brl(v)}" for k, v in d["conta"]["ajustes"].items()]
    L.append(f"  {'= Resultado':<30}{_brl(d['resultado']) if d['resultado'] is not None else '      — (faltam custos de SKUs)'}")
    if d["nao_tratadas"]:
        L.append(f"\n  Listas do Finances não tratadas (ver se importam): {d['nao_tratadas']}")
    L += ["", f"  {'SKU':<24}{'un.liq':>7}{'rec.liq':>11}{'após taxas':>12}{'margem':>11}{'%':>7}"]
    for sku, x in sorted(r["skus"].items(), key=lambda kv: -kv[1]["receita_liquida"]):
        pct = f"{x['margem_pct']:.1f}" if x["margem_pct"] is not None else "—"
        L.append(f"  {sku[:23]:<24}{x['un_liquidas']:>7.0f}{_brl(x['receita_liquida']):>11}"
                 f"{_brl(x['margem_apos_taxas']):>12}{_brl(x['margem']):>11}{pct:>7}")
    return "\n".join(L)


def main(argv: list) -> int:
    ids = [a for a in argv if not a.startswith("--")]
    conta_id = ids[0] if ids else "amazon02"
    dias = int(argv[argv.index("--dias") + 1]) if "--dias" in argv else 30
    try:
        print(formatar(apurar(conta_id, dias)))
    except api.ErroAmazon as e:
        print(f"✗ {e}")
        return 2 if e.status == 502 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
