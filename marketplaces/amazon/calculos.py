"""
marketplaces/amazon/calculos.py — Matemática do módulo Amazon (sem rede, sem disco)
MZCell Rentabilidade

Tudo aqui é determinístico e testável sozinho. O acesso à API fica em api.py.
"""

from datetime import date, datetime, timedelta, timezone


def agrupar_estoque_por_asin(linhas: list) -> list:
    """
    Consolida o inventário FBA (uma linha por seller SKU) em uma linha por ASIN.

    O mesmo ASIN pode ter vários SKUs FBA (ex.: KIT3TAP-FBA e KIT3-TAPMAG-SORTIDO em
    B0GXX6NG28). As vendas são apuradas por ASIN, então uma linha por SKU faria cada
    uma receber a venda cheia do ASIN e dobraria a meta de estoque.
    """
    grupos = {}
    for linha in linhas:
        g = grupos.get(linha["asin"])
        if g is None:
            grupos[linha["asin"]] = g = {**linha, "skus": []}
        else:
            g["estoque_valido"] += linha["estoque_valido"]
            g["disponivel"] += linha["disponivel"]
            g["em_transito"] += linha["em_transito"]
            if not g["titulo"]:
                g["titulo"] = linha["titulo"]
        if linha["sku"]:
            g["skus"].append(linha["sku"])

    for g in grupos.values():
        g["skus"] = sorted(set(g["skus"]))
        g["sku"] = " + ".join(g["skus"])
    return list(grupos.values())


def linha_de_estoque(resumo: dict) -> dict:
    """
    Converte um item de inventorySummaries da SP-API no formato do app.

    Estoque válido = disponível + em trânsito (working + shipped + receiving).
    Reservado e não vendável ficam de fora: não podem ser vendidos. Incluir o que já
    está a caminho evita reenviar o mesmo lote.
    """
    d = resumo.get("inventoryDetails") or {}
    disponivel = d.get("fulfillableQuantity") or 0
    em_transito = ((d.get("inboundWorkingQuantity") or 0)
                   + (d.get("inboundShippedQuantity") or 0)
                   + (d.get("inboundReceivingQuantity") or 0))
    return {
        "asin": resumo.get("asin", ""),
        "sku": resumo.get("sellerSku", ""),
        "titulo": resumo.get("productName", ""),
        "fnsku": resumo.get("fnSku", ""),
        "disponivel": disponivel,
        "em_transito": em_transito,
        "estoque_valido": disponivel + em_transito,
    }


def _data_do_bucket(bucket: dict):
    """Data inicial do bucket ('2026-09-14T00:00:00-07:00--2026-09-15T...'). None se ilegível."""
    try:
        return date.fromisoformat(str(bucket.get("interval", ""))[:10])
    except ValueError:
        return None


def velocidade(buckets: list, hoje: date, peso_7d: float = 0.40, peso_30d: float = 0.60) -> dict:
    """
    Velocidade de venda a partir dos buckets diários do orderMetrics.

    Os 7 dias são os que começam a menos de 7 dias de `hoje`, medidos pela data do
    bucket e não pela posição na lista: se a resposta omitir dias sem venda, "os
    últimos 7 itens" cobriria uma janela maior e inflaria a velocidade.
    Peso da Amazon (doc 04): 7d × 40% + 30d × 60%.
    """
    corte = hoje - timedelta(days=7)
    vendido_30d = vendido_7d = 0
    for b in buckets:
        und = b.get("unitCount") or 0
        vendido_30d += und
        d = _data_do_bucket(b)
        if d is not None and d >= corte:
            vendido_7d += und

    vel_7d, vel_30d = vendido_7d / 7, vendido_30d / 30
    return {
        "vendido_7d": vendido_7d,
        "vendido_30d": vendido_30d,
        "velocidade_7d": round(vel_7d, 3),
        "velocidade_30d": round(vel_30d, 3),
        "velocidade": round(vel_7d * peso_7d + vel_30d * peso_30d, 3),
    }


# ─── Conciliação de pedidos ──────────────────────────────────────────────────

def _quando(texto: str):
    """purchase-date do relatório de pedidos ('2026-09-14T03:15:22+00:00'). Sem fuso = UTC."""
    dt = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _inteiro(texto) -> int:
    try:
        return int(float(texto))
    except (TypeError, ValueError):
        return 0


def conciliar_pedidos(linhas: list, asin: str, de: date, ate: date, fuso: timezone) -> dict:
    """
    Soma as linhas do relatório de pedidos de um ASIN, dia fechado em `fuso`,
    de `de` a `ate` (inclusive nas duas pontas).

    Devolve as unidades de três formas — todas, só as canceladas e as líquidas — porque o
    número do Seller Central e o do orderMetrics podem seguir regras diferentes, e a única
    forma de saber qual bate é ver as três lado a lado. Soma por ASIN (todos os SKUs e
    logísticas), como as vendas do projeto.
    """
    faltam = [c for c in ("purchase-date", "asin", "quantity") if linhas and c not in linhas[0]]
    if faltam:
        raise ValueError(f"Relatório sem as colunas {faltam}; colunas recebidas: {sorted(linhas[0])}")

    inicio = datetime(de.year, de.month, de.day, tzinfo=fuso)
    fim = datetime(ate.year, ate.month, ate.day, tzinfo=fuso) + timedelta(days=1)

    itens, pedidos = [], set()
    for l in linhas:
        if l.get("asin", "").strip().upper() != asin.upper():
            continue
        quando = _quando(l["purchase-date"]).astimezone(fuso)
        if not (inicio <= quando < fim):
            continue
        status = l.get("order-status") or l.get("item-status") or "?"
        cancelada = "cancel" in f"{l.get('order-status', '')} {l.get('item-status', '')}".lower()
        itens.append({"pedido": l.get("amazon-order-id", ""), "quando": quando, "status": status,
                      "cancelada": cancelada, "sku": l.get("sku", ""),
                      "canal": l.get("fulfillment-channel", ""), "qtd": _inteiro(l["quantity"])})
        pedidos.add(l.get("amazon-order-id", ""))

    itens.sort(key=lambda i: i["quando"])
    por_status, por_sku, por_dia = {}, {}, {}
    for i in itens:
        por_status[i["status"]] = por_status.get(i["status"], 0) + i["qtd"]
        s = por_sku.setdefault((i["sku"], i["canal"]), 0)
        por_sku[(i["sku"], i["canal"])] = s + i["qtd"]
        d = por_dia.setdefault(i["quando"].date(), {"total": 0, "canceladas": 0})
        d["total"] += i["qtd"]
        if i["cancelada"]:
            d["canceladas"] += i["qtd"]

    total = sum(i["qtd"] for i in itens)
    canceladas = sum(i["qtd"] for i in itens if i["cancelada"])
    return {"asin": asin, "de": de, "ate": ate, "pedidos": len(pedidos), "linhas": len(itens),
            "unidades_total": total, "unidades_canceladas": canceladas,
            "unidades_liquidas": total - canceladas, "por_status": por_status,
            "por_sku": por_sku, "por_dia": por_dia, "itens": itens}
