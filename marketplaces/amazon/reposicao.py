"""
marketplaces/amazon/reposicao.py — Reposição de estoque FBA (somente leitura)
MZCell Rentabilidade

Uso:  .\\venv\\Scripts\\python.exe -m marketplaces.amazon.reposicao amazon02 [--meta 20] [--sem-amazon]

Regras:
  - Estoque válido = disponível + em trânsito (working + shipped + receiving), consolidado por ASIN
    (api.estoque_fba). O que já está a caminho NÃO é reenviado.
  - Velocidade = 7d × 40% + 30d × 60%, por ASIN, somando FBA e DBA (orderMetrics, dias fechados de
    Brasília). Mesma apuração do módulo de vendas e da conferência.
  - Meta = `meta_dias` (padrão 20, igual ao Full do ML; mude em amazon_config.json → parametros.meta_dias)
    × velocidade. Sugestão = max(0, meta − estoque válido), arredondada para cima.
  - Cobertura = estoque válido ÷ velocidade, em dias (None se não há venda).
  - Conferência com a Amazon (GET_FBA_INVENTORY_PLANNING_DATA): traz as unidades ENVIADAS em 7 e 30 dias
    (só FBA) para comparar com a nossa velocidade. Neste marketplace a Amazon NÃO entrega recomendação de
    reposição (recommended-order-quantity e days-of-supply vêm vazios, validado em 15/10/2026), então a
    sugestão é só nossa.
"""

import math
import sys
import time

from marketplaces.amazon import api, relatorios

META_DIAS_PADRAO = 20
PLANEJAMENTO = "GET_FBA_INVENTORY_PLANNING_DATA"


def sugerir(item: dict, vel: dict, meta_dias: float) -> dict:
    """Um ASIN: estoque consolidado (calculos.linha_de_estoque) + velocidade (calculos.velocidade)."""
    demanda = vel["velocidade"]
    estoque = item["estoque_valido"]
    meta = demanda * meta_dias
    sugestao = max(0, math.ceil(meta - estoque))
    cobertura = round(estoque / demanda, 1) if demanda > 0 else None
    cobertura_disp = round(item["disponivel"] / demanda, 1) if demanda > 0 else None

    if demanda == 0:
        situacao = "SEM VENDA"
    elif item["disponivel"] == 0:
        situacao = "RUPTURA" if item["em_transito"] == 0 else "SEM ESTOQUE (A CAMINHO)"
    elif sugestao > 0:
        situacao = "REPOR"
    else:
        situacao = "OK"

    v30 = vel["velocidade_30d"]
    tendencia = round(vel["velocidade_7d"] / v30, 2) if v30 > 0 else None
    return {
        "asin": item["asin"], "sku": item["sku"], "titulo": item["titulo"],
        "disponivel": item["disponivel"], "em_transito": item["em_transito"], "estoque_valido": estoque,
        "vendido_7d": vel["vendido_7d"], "vendido_30d": vel["vendido_30d"],
        "velocidade": demanda, "tendencia_7d_30d": tendencia,
        "meta_unidades": round(meta, 1), "sugestao": sugestao,
        "cobertura_dias": cobertura, "cobertura_disponivel_dias": cobertura_disp,
        "situacao": situacao,
    }


def ordenar(linhas: list) -> list:
    """Mais urgente primeiro: menor cobertura (ruptura = 0); sem venda por último."""
    def chave(l):
        if l["situacao"] == "SEM VENDA":
            return (1, 0)
        return (0, 0 if l["situacao"].startswith(("RUPTURA", "SEM ESTOQUE")) else l["cobertura_dias"])
    return sorted(linhas, key=chave)


def comparar_amazon(linhas: list, planejamento: list) -> list:
    """Acrescenta o que a Amazon enviou em 7/30 dias (somado entre os SKUs do ASIN) para conferir a velocidade."""
    por_asin = {}
    for p in planejamento:
        a = por_asin.setdefault(p.get("asin", ""), {"t7": 0, "t30": 0, "alerta": set()})
        a["t7"] += _num(p.get("units-shipped-t7"))
        a["t30"] += _num(p.get("units-shipped-t30"))
        if p.get("alert"):
            a["alerta"].add(p["alert"])
    for l in linhas:
        a = por_asin.get(l["asin"])
        l["amazon_enviado_7d"] = a["t7"] if a else None
        l["amazon_enviado_30d"] = a["t30"] if a else None
        l["amazon_alerta"] = ", ".join(sorted(a["alerta"])) if a else None
    return linhas


def _num(v, padrao=0):
    try:
        return float(str(v).replace(",", ".")) if str(v).strip() != "" else padrao
    except ValueError:
        return padrao


def apurar(conta_id: str, meta_dias: float = None, com_amazon: bool = True, progresso=None) -> dict:
    par = api.load_config().get("parametros", {})
    meta_dias = float(meta_dias if meta_dias is not None else par.get("meta_dias", META_DIAS_PADRAO))
    log = progresso or (lambda m: None)

    itens = api.estoque_fba(conta_id)
    log(f"{len(itens)} ASINs no FBA; apurando vendas...")
    linhas = []
    for n, item in enumerate(itens, 1):
        vel = api.vendas_asin(conta_id, item["asin"])
        linhas.append(sugerir(item, vel, meta_dias))
        time.sleep(0.5)                     # orderMetrics: 0,5 req/s
        if n % 10 == 0:
            log(f"  {n}/{len(itens)}")

    comparou = False
    if com_amazon:
        try:
            rid = relatorios.criar(conta_id, PLANEJAMENTO)
            doc = relatorios.aguardar(conta_id, rid, intervalo=10, tentativas=36)
            comparar_amazon(linhas, relatorios.ler_tsv(relatorios.baixar_texto(conta_id, doc)))
            comparou = True
        except api.ErroAmazon as e:
            log(f"comparativo com a Amazon indisponível: {e}")
    linhas = ordenar(linhas)
    return {"conta": conta_id, "meta_dias": meta_dias, "comparativo_amazon": comparou,
            "total_asins": len(linhas),
            "a_repor": sum(1 for l in linhas if l["sugestao"] > 0),
            "unidades_sugeridas": sum(l["sugestao"] for l in linhas), "itens": linhas}


def formatar(r: dict) -> str:
    L = [f"Reposição FBA — {r['conta']} — meta {r['meta_dias']:g} dias",
         f"{r['a_repor']} de {r['total_asins']} ASINs precisam repor ({r['unidades_sugeridas']} unidades)", ""]
    cab = f"{'ASIN':<12}{'SKU':<22}{'disp':>6}{'trânsito':>9}{'vel/dia':>9}{'cobert.':>9}{'sugestão':>9}  situação"
    if r["comparativo_amazon"]:
        cab += "   | Amazon enviou 7d/30d"
    L += [cab, "-" * len(cab)]
    for l in r["itens"]:
        cob = f"{l['cobertura_dias']:.0f}d" if l["cobertura_dias"] is not None else "—"
        linha = (f"{l['asin']:<12}{l['sku'][:21]:<22}{l['disponivel']:>6}{l['em_transito']:>9}"
                 f"{l['velocidade']:>9.2f}{cob:>9}{l['sugestao']:>9}  {l['situacao']}")
        if r["comparativo_amazon"]:
            e7, e30 = l.get("amazon_enviado_7d"), l.get("amazon_enviado_30d")
            linha += f"   | {'—' if e7 is None else f'{e7:.0f}'} / {'—' if e30 is None else f'{e30:.0f}'}"
            if l.get("amazon_alerta"):
                linha += f"  ({l['amazon_alerta']})"
        L.append(linha)
    return "\n".join(L)


def main(argv: list) -> int:
    ids = [a for a in argv if not a.startswith("--")]
    conta_id = ids[0] if ids else "amazon02"
    meta = float(argv[argv.index("--meta") + 1]) if "--meta" in argv else None
    try:
        r = apurar(conta_id, meta, "--sem-amazon" not in argv, progresso=print)
    except api.ErroAmazon as e:
        print(f"✗ {e}")
        return 2 if e.status == 502 else 1
    print("\n" + formatar(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
