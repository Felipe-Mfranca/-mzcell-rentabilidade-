"""
marketplaces/amazon/conferencia.py — Confere as vendas de um ASIN contra as linhas de pedido
MZ App

Uso:  .\\venv\\Scripts\\python.exe -m marketplaces.amazon.conferencia amazon02 B0DZN43HGG --sc 164

Baixa o relatório de pedidos (uma linha por item vendido, com status, data e quantidade),
soma as unidades do ASIN e mostra lado a lado: o número do Seller Central (--sc), o do
orderMetrics que o módulo usa para a velocidade, e o que as linhas de pedido de fato somam
— com e sem cancelados. É a prova independente de que a velocidade está certa.

Janela padrão: os 30 dias fechados que terminam ontem, no fuso de parametros.fuso (Brasília).
O relatório aceita no máximo 30 dias por pedido.
"""

import csv
import sys
from datetime import date, datetime, timedelta, timezone

from marketplaces.amazon import api, calculos, relatorios

TIPO_PEDIDOS = "GET_FLAT_FILE_ALL_ORDERS_DATA_BY_ORDER_DATE_GENERAL"
LIMITE_DIAS = 30


def _fuso(conta_id: str):
    nome = api.load_config().get("parametros", {}).get("fuso", "America/Sao_Paulo")
    if nome not in api.FUSOS:
        raise api.ErroAmazon(f"parametros.fuso deve ser um de: {', '.join(api.FUSOS)}", 400)
    return nome, api.FUSOS[nome]


def janela_padrao(fuso) -> tuple:
    """Os 30 dias fechados que terminam ontem (o dia de hoje, em andamento, fica de fora)."""
    ate = datetime.now(fuso).date() - timedelta(days=1)
    return ate - timedelta(days=LIMITE_DIAS - 1), ate


def _utc(d: date, fuso) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=fuso).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def conferir(conta_id: str, asin: str, de: date = None, ate: date = None, progresso=None) -> dict:
    fuso_nome, fuso = _fuso(conta_id)
    padrao = janela_padrao(fuso)
    de, ate = de or padrao[0], ate or padrao[1]
    dias = (ate - de).days + 1
    if dias < 1:
        raise api.ErroAmazon("A data final é anterior à inicial", 400)
    if dias > LIMITE_DIAS:
        raise api.ErroAmazon(f"O relatório aceita no máximo {LIMITE_DIAS} dias; a janela pedida tem {dias}", 400)

    if progresso:
        progresso(f"Pedindo o relatório de pedidos ({de} a {ate}, {fuso_nome})...")
    rid = relatorios.criar(conta_id, TIPO_PEDIDOS, _utc(de, fuso), _utc(ate + timedelta(days=1), fuso))
    doc = relatorios.aguardar(conta_id, rid, progresso)
    if progresso:
        progresso("Baixando e somando...")
    linhas = relatorios.ler_tsv(relatorios.baixar_texto(conta_id, doc))

    resultado = calculos.conciliar_pedidos(linhas, asin, de, ate, fuso)
    resultado.update(conta=conta_id, fuso=fuso_nome, linhas_relatorio=len(linhas), dias=dias,
                     ordermetrics=None, ordermetrics_erro=None)
    # O orderMetrics do módulo só é comparável na janela padrão. É comparação secundária:
    # se falhar, o resultado do relatório (que levou minutos) não pode ser perdido.
    if (de, ate) == padrao:
        try:
            resultado["ordermetrics"] = api.vendas_asin(conta_id, asin)["vendido_30d"]
        except api.ErroAmazon as e:
            resultado["ordermetrics_erro"] = str(e)
    # unidades compradas entre 21h e 24h: são as que trocam de dia entre UTC e Brasília
    resultado["noite"] = sum(i["qtd"] for i in resultado["itens"] if i["quando"].hour >= 21)
    return resultado


def formatar(r: dict, sc: int = None) -> str:
    L = [f"Conferência de pedidos — ASIN {r['asin']} — {r['conta']}",
         f"Janela: {r['de']:%d/%m/%Y} a {r['ate']:%d/%m/%Y} ({r['dias']} dias fechados, {r['fuso']})",
         f"Relatório: {r['linhas_relatorio']} linhas no total; {r['linhas']} do ASIN, em {r['pedidos']} pedidos", "",
         f"  {'FONTE':<52}{'UNIDADES':>9}", "  " + "─" * 61]
    fontes = []
    if sc is not None:
        fontes.append(("Seller Central (informado em --sc)", sc))
    if r["ordermetrics"] is not None:
        fontes.append(("orderMetrics (o que o módulo usa na velocidade)", r["ordermetrics"]))
    fontes += [("Linhas de pedido — todas as unidades", r["unidades_total"]),
               ("Linhas de pedido — só as canceladas", r["unidades_canceladas"]),
               ("Linhas de pedido — líquidas (sem canceladas)", r["unidades_liquidas"])]
    L += [f"  {nome:<52}{valor:>9}" for nome, valor in fontes]

    L += ["", "Leitura:"]
    candidatos = (("todas as unidades", r["unidades_total"]), ("as líquidas", r["unidades_liquidas"]))
    for nome, ref in fontes[: len(fontes) - 3]:
        bate = [n for n, v in candidatos if v == ref]
        if bate:
            L.append(f"  • {nome.split(' (')[0]} ({ref}) bate exatamente com {' e '.join(bate)}.")
        else:
            dif = "; ".join(f"{ref - v:+d} contra {n}" for n, v in candidatos)
            L.append(f"  • {nome.split(' (')[0]} ({ref}) não bate com nenhuma ({dif}).")
    if r.get("ordermetrics_erro"):
        L.append(f"  • Não consegui consultar o orderMetrics para comparar: {r['ordermetrics_erro']}")
    L.append(f"  • {r['noite']} unidades foram compradas entre 21h e 24h (Brasília): são as que mudam de dia "
             "entre UTC e Brasília.")

    L += ["", "Por status:"] + [f"  {s:<30}{u:>6}" for s, u in sorted(r["por_status"].items())]
    L += ["", "Por SKU e logística:"] + [f"  {sku or '?':<28}{canal or '?':<10}{u:>6}"
                                         for (sku, canal), u in sorted(r["por_sku"].items())]
    L += ["", f"  {'DIA':<14}{'TOTAL':>7}{'CANCEL.':>9}{'LÍQ.':>7}"]
    for dia, d in sorted(r["por_dia"].items()):
        L.append(f"  {dia:%d/%m/%Y}    {d['total']:>7}{d['canceladas']:>9}{d['total'] - d['canceladas']:>7}")
    return "\n".join(L)


def salvar_csv(r: dict) -> str:
    caminho = f"conferencia_{r['asin']}.csv"
    with open(caminho, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["pedido", "data_hora_local", "status", "sku", "canal", "quantidade", "cancelada"])
        for i in r["itens"]:
            w.writerow([i["pedido"], i["quando"].strftime("%d/%m/%Y %H:%M:%S"), i["status"], i["sku"],
                        i["canal"], i["qtd"], "sim" if i["cancelada"] else "não"])
    return caminho


def main(argv: list) -> int:
    pos = [a for a in argv if not a.startswith("--")]

    def opcao(nome):
        return argv[argv.index(nome) + 1] if nome in argv and argv.index(nome) + 1 < len(argv) else None

    # valores das opções não contam como argumentos posicionais
    valores = {opcao(n) for n in ("--sc", "--de", "--ate") if opcao(n)}
    pos = [a for a in pos if a not in valores]
    if len(pos) < 2:
        print("Uso: python -m marketplaces.amazon.conferencia <conta> <ASIN> [--sc N] [--de AAAA-MM-DD --ate AAAA-MM-DD]")
        return 1
    conta_id, asin = pos[0], pos[1]
    try:
        sc = int(opcao("--sc")) if opcao("--sc") else None
        de = date.fromisoformat(opcao("--de")) if opcao("--de") else None
        ate = date.fromisoformat(opcao("--ate")) if opcao("--ate") else None
        r = conferir(conta_id, asin, de, ate, progresso=print)
    except ValueError as e:
        print(f"✗ Argumento inválido: {e}")
        return 1
    except api.ErroAmazon as e:
        print(f"✗ {e}")
        return 2 if e.status == 502 else 1
    print()
    print(formatar(r, sc))
    print(f"\nPedido a pedido salvo em {salvar_csv(r)} (abra no Excel)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
