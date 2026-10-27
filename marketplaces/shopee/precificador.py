"""
marketplaces/shopee/precificador.py — Precificador da Shopee (cálculo puro, sem rede)
MZ App

Entrada: preço de venda, ROAS objetivo, CMV, imposto (da empresa dona da conta) e frete unitário.
Saída: detalhamento por unidade e margem. O ADS por unidade é preço ÷ ROAS (ROAS = receita ÷ investimento).

Tabela de comissão da Shopee (faixas por preço do item), conforme artigos de mercado de 2026 (ver
TABELA_FONTE). Não é a tabela oficial: as faixas e a taxa fixa de R$ 4,00 x R$ 4,50 divergem entre fontes.
Conferir na Central do Vendedor antes de usar em decisão de preço.
"""

# (preço mínimo, preço máximo, comissão %, taxa fixa por item em R$)
# Até R$ 8 a fonte indica 50% e sem taxa fixa; o limite (R$ 8 ou R$ 9) também diverge entre fontes.
TABELA_COMISSAO = [
    (0.00, 7.99, 0.50, 0.00),
    (8.00, 79.99, 0.20, 4.00),
    (80.00, 99.99, 0.14, 16.00),
    (100.00, 199.99, 0.14, 20.00),
    (200.00, float("inf"), 0.14, 26.00),
]
TABELA_FONTE = "Artigos de mercado de 2026 (lider10, calcularte, mambadigital, roilabs, multdrop). Provisória: conferir na Central do Vendedor."
TABELA_PROVISORIA = True


def faixa(preco: float):
    for minimo, maximo, pct, fixa in TABELA_COMISSAO:
        if minimo <= preco <= maximo:
            return pct, fixa
    return TABELA_COMISSAO[-1][2], TABELA_COMISSAO[-1][3]


def simular(preco: float, roas: float, cmv: float = 0.0, imposto_pct: float = 0.0, frete: float = 0.0,
            destaque_pct: float = 0.0, devolucao: float = 0.0) -> dict:
    """Detalhamento por unidade. ROAS 0 = sem ADS. Preço 0 não tem como calcular margem."""
    if preco <= 0:
        return {"erro": "Informe o preço de venda"}
    pct, fixa = faixa(preco)
    comissao = round(preco * pct, 2)
    taxa_fixa = round(fixa, 2)
    imposto = round(preco * imposto_pct, 2)
    ads = round(preco / roas, 2) if roas and roas > 0 else 0.0
    destaque = round(preco * destaque_pct, 2)
    devolucao = round(devolucao, 2)
    custos = comissao + taxa_fixa + imposto + round(cmv, 2) + round(frete, 2) + ads + destaque + devolucao
    margem = round(preco - custos, 2)
    return {
        "preco": round(preco, 2),
        "comissao_pct": pct,
        "comissao": comissao,
        "taxa_fixa": taxa_fixa,
        "imposto_pct": round(imposto_pct, 6),
        "imposto": imposto,
        "cmv": round(cmv, 2),
        "frete": round(frete, 2),
        "roas_objetivo": round(roas, 2) if roas else 0.0,
        "ads": ads,
        "destaque_pct": round(destaque_pct, 6),
        "destaque": destaque,
        "devolucao": devolucao,
        "custos_total": round(custos, 2),
        "margem": margem,
        "margem_pct": round(margem / preco, 4),
        "preco_equilibrio": preco_equilibrio(cmv, imposto_pct, frete, roas, destaque_pct, devolucao),
        "tabela_provisoria": TABELA_PROVISORIA,
    }


def preco_equilibrio(cmv: float, imposto_pct: float, frete: float, roas: float,
                     destaque_pct: float = 0.0, devolucao: float = 0.0):
    """Menor preço em que a margem fica em zero para o ROAS informado. Dentro de cada faixa a margem é linear
    (comissão e taxa fixa são constantes), então a raiz é procurada faixa a faixa; os saltos da taxa fixa
    entre faixas são tratados pela própria varredura."""
    def margem(p, pct, fixa):
        ads = p / roas if roas and roas > 0 else 0.0
        return p - (p * pct + fixa + p * imposto_pct + cmv + frete + ads + p * destaque_pct + devolucao)
    for minimo, maximo, pct, fixa in TABELA_COMISSAO:
        lo, hi = max(minimo, 0.01), min(maximo, 5000.0)
        if lo > hi or margem(hi, pct, fixa) < 0:
            continue
        if margem(lo, pct, fixa) >= 0:
            return round(lo, 2)
        for _ in range(60):
            meio = (lo + hi) / 2
            if margem(meio, pct, fixa) >= 0:
                hi = meio
            else:
                lo = meio
        return round(hi, 2)
    return None
