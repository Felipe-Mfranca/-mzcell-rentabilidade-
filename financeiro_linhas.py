"""Classificação das transações do Mercado Livre nas linhas da DRE (categorias 01 a 10).

Regra do Grupo: toda transação entra em uma linha. Não existe "outros": o que não tem regra
aparece como pendência e o fechamento não passa enquanto houver pendência.

A tabela segue o que a DRE de agosto/2026 lançou (DRE_Agosto2026_Mzcell_v3.xlsx, abas 01 a 08).
Só leitura: não grava nada e não fala com a API.

Uso:  python financeiro_linhas.py caminho\\movimentacao.xlsx
"""
import sys
from collections import defaultdict

LINHAS = {
    "01": "Receita Operacional",
    "02": "Deduções e Impostos",
    "03": "CMV + Custo Operacional de Marketplace",
    "04": "Despesa Administrativa",
    "05": "Despesa Operacional / Comercial",
    "06": "Despesa Financeira",
    "07": "Receita Não Operacional",
    "08": "Transacional (fora do resultado)",
    "09": "Distribuição / Participação (fora do resultado)",
    "10": "Compra de Estoque (fora do resultado)",
}
NO_RESULTADO = ("01", "02", "03", "04", "05", "06", "07")

# Tipo de operação do relatório de movimentação do Meli -> (linha, subcategoria)
TIPOS_MELI = {
    # 01 Receita
    "Recebimento": ("01", "Venda"),
    "Entrada de dinheiro extra": ("01", "Entrada de dinheiro extra"),
    "Recebimento pelo desconto da sua contraparte": ("01", "Desconto da contraparte recebido"),
    "Desconto recebido": ("01", "Desconto recebido"),
    # 02 Devoluções ao comprador
    "Devolução por Compra Garantida": ("02", "Devolução por Compra Garantida"),
    "Devolução de recebimento": ("02", "Devolução de recebimento"),
    "Devolução parcial de recebimento": ("02", "Devolução de recebimento"),
    "Cancelamento de entrada de dinheiro extra": ("02", "Cancelamento de entrada de dinheiro extra"),
    "Cancelamento de devolução por Compra Garantida": ("02", "Cancelamento de devolução por Compra Garantida"),
    # 03 Custo de marketplace (tarifas, frete cobrado na venda, parcelamento, cancelamentos de custo)
    "Tarifa por vender no Mercado Livre": ("03", "Tarifa por vender"),
    "Cancelamento de tarifa por vender no Mercado Livre": ("03", "Tarifa por vender"),
    "Tarifa de processamento": ("03", "Tarifa de processamento"),
    "Cancelamento do custo de processamento": ("03", "Tarifa de processamento"),
    "Reembolso do frete": ("03", "Frete cobrado na venda"),
    "Custo de envio por Fulfillment": ("03", "Frete Fulfillment"),
    "Cancelamento do custo de envio por Fulfillment": ("03", "Frete Fulfillment"),
    "Custo de envio por cross-docking": ("03", "Frete cross-docking"),
    "Cancelamento do custo de envio": ("03", "Frete cobrado na venda"),
    "Custos de parcelamento": ("03", "Parcelamento"),
    "Cancelamento de custos de parcelamento": ("03", "Parcelamento"),
    "Custo por absorção da tarifa parcelamento": ("03", "Parcelamento"),
    "Cancelamento de custo por absorção da tarifa parcelamento": ("03", "Parcelamento"),
    "Cancelamento parcial de custo por absorção da tarifa parcelamento": ("03", "Parcelamento"),
    "Dinheiro recebido": ("03", "Dinheiro recebido"),
    "Custo pela devolução": ("03", "Custo pela devolução"),
    "Cancelamento de custo pela devolução": ("03", "Custo pela devolução"),
    "Desconto para a sua contraparte": ("03", "Desconto para a contraparte"),
    "Cancelamento de desconto para a sua contraparte": ("03", "Desconto para a contraparte"),
    # 08 Funding e repasses (fora do resultado)
    "Pagamento": ("08", "Funding - Pagamento"),
    "Adição de dinheiro": ("08", "Funding - Adição de dinheiro"),
    "Devolução de adição de dinheiro": ("08", "Funding - Devolução de adição"),
    "Devolução de pagamento": ("08", "Funding - Devolução de pagamento"),
    "Transferência via Pix": ("08", "Repasse Meli"),
    "Pagamento com desconto recebido": ("08", "Funding - Pagamento com desconto recebido"),
    "Devolução parcial de pagamento": ("08", "Funding - Devolução de pagamento"),
    "Devolução parcial de pagamento com desconto recebido": ("08", "Funding - Devolução de pagamento com desconto"),
    "Devolução parcial de adição de dinheiro": ("08", "Funding - Devolução de adição"),
    "Devolução parcial de desconto recebido": ("08", "Funding - Devolução de desconto recebido"),
}

# Tipos que dependem da operação: entram em 03 quando a mesma operação tem venda/devolução
TIPO_CONTEXTUAL = "Movimentação geral"
SUB_CONTEXTUAL_VENDA = ("03", "Movimentação geral junto de venda")
SUB_CONTEXTUAL_ISOLADA = ("08", "Movimentação geral isolada")


def classificar(movs):
    """movs: lista de dicts com 'tipo', 'operacao' e 'valor'.
    Devolve a mesma lista com 'linha' e 'subcategoria' (None quando não há regra)."""
    com_venda = {
        m["operacao"] for m in movs
        if TIPOS_MELI.get(m["tipo"], (None,))[0] in ("01", "02", "03")
    }
    saida = []
    for m in movs:
        if m["tipo"] == TIPO_CONTEXTUAL:
            linha, sub = SUB_CONTEXTUAL_VENDA if m["operacao"] in com_venda else SUB_CONTEXTUAL_ISOLADA
        else:
            linha, sub = TIPOS_MELI.get(m["tipo"], (None, None))
        saida.append({**m, "linha": linha, "subcategoria": sub})
    return saida


def resumir(classificados):
    """Totais por linha e subcategoria, resultado da DRE e pendências (sem linha)."""
    linhas = {k: {"valor": 0.0, "n": 0, "subcategorias": defaultdict(float)} for k in LINHAS}
    pendencias = defaultdict(lambda: {"n": 0, "valor": 0.0})
    for m in classificados:
        if m["linha"] is None:
            p = pendencias[m["tipo"]]
            p["n"] += 1
            p["valor"] += m["valor"]
            continue
        l = linhas[m["linha"]]
        l["valor"] += m["valor"]
        l["n"] += 1
        l["subcategorias"][m["subcategoria"]] += m["valor"]
    resultado = sum(linhas[k]["valor"] for k in NO_RESULTADO)
    return {"linhas": linhas, "resultado": resultado, "pendencias": dict(pendencias)}


def ler_movimentacao(caminho):
    """Lê o relatório de movimentação do Meli (xlsx). Valor vem como texto com ponto decimal."""
    import openpyxl
    wb = openpyxl.load_workbook(caminho, read_only=True, data_only=True)
    linhas = wb.active.iter_rows(values_only=True)
    next(linhas)  # cabeçalho
    movs = []
    for r in linhas:
        if not r or r[1] is None:
            continue
        movs.append({
            "data": str(r[0]),
            "tipo": str(r[1]),
            "movimento": str(r[2]),
            "operacao": str(r[3]),
            "valor": float(r[4] or 0),
        })
    return movs


def imprimir(res):
    for k, nome in LINHAS.items():
        l = res["linhas"][k]
        marca = "" if k in NO_RESULTADO else "  (fora do resultado)"
        print(f"{k} {nome:<42} {l['valor']:>16,.2f}  {l['n']:>7} lanç.{marca}")
        for sub, v in sorted(l["subcategorias"].items(), key=lambda x: -abs(x[1])):
            print(f"      {sub:<46} {v:>14,.2f}")
    print("\nResultado (01 a 07): " + f"{res['resultado']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    if res["pendencias"]:
        print("\nSEM LINHA (definir antes do fechamento):")
        for tipo, p in res["pendencias"].items():
            print(f"  {tipo}: {p['n']} lanç., " + f"{p['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
    else:
        print("\nTodas as transações têm linha.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Uso: python financeiro_linhas.py caminho\\movimentacao.xlsx")
    imprimir(resumir(classificar(ler_movimentacao(sys.argv[1]))))
