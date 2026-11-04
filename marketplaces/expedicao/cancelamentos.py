"""
marketplaces/expedicao/cancelamentos.py — Pedidos cancelados no ML que seguem ativos no Bling (somente leitura)
MZ App

O aviso não é gravado: a cada consulta o ML é comparado com o Bling, então ele some sozinho quando o pedido
no Bling passa para Cancelado. Só entram Flex e Places (Full não passa pelo Bling) e pedidos que existem no Bling.
Pedido de pacote: o Bling guarda o número do pacote em numeroLoja. Se o pacote ainda tem outro pedido ativo,
o aviso vira "ajustar itens" (cancelar o pedido inteiro do Bling seria errado).
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import ml_api
from marketplaces.bling import api as bling
from marketplaces.expedicao.ml import BRT, CANAIS
from reposicao_full import ML

PARES = {"meli01": "bling01", "meli02": "bling02", "meli03": "bling03"}
SITUACAO_CANCELADO = 12
SITUACOES = {6: "Em aberto", 9: "Atendido (NF emitida)", 15: "Em andamento", 21: "Em digitação",
             24: "Verificado (checkout feito)", 753338: "Pagamento aprovado", 753339: "Em devolução"}
BLING_VENDA_URL = "https://www.bling.com.br/vendas.php#edit/{id}"
DIAS = 7
_cache = {}
CACHE_SEG = 120


def _pedidos_bling(conta_bling: str, dias: int) -> dict:
    de = (datetime.now(BRT) - timedelta(days=dias + 2)).strftime("%Y-%m-%d")
    ate = (datetime.now(BRT) + timedelta(days=1)).strftime("%Y-%m-%d")
    por_loja = {}
    for pg in range(1, 40):
        st, r = bling.get(conta_bling, "/pedidos/vendas", dataInicial=de, dataFinal=ate, pagina=pg, limite=100)
        if st != 200:
            raise RuntimeError(f"{conta_bling}: pedidos HTTP {st}")
        dados = r.get("data", [])
        for p in dados:
            por_loja[str(p.get("numeroLoja"))] = p
        if len(dados) < 100:
            break
    return por_loja


def _conta(conta: str, dias: int) -> list:
    ml = ML(conta)
    sid = ml_api.get_seller_id(conta)
    de = (datetime.now(BRT) - timedelta(days=dias)).strftime("%Y-%m-%dT00:00:00.000-03:00")
    cancelados = []
    for pg in range(20):
        st, b = ml.get("/orders/search", seller=sid, sort="date_desc", limit=50, offset=pg * 50,
                       **{"order.status": "cancelled", "order.date_created.from": de})
        if st != 200:
            raise RuntimeError(f"{conta}: pedidos cancelados HTTP {st}")
        res = b.get("results") or []
        cancelados += res
        if len(res) < 50:
            break
    if not cancelados:
        return []
    bling_por_loja = _pedidos_bling(PARES[conta], dias)

    def tem_outro_ativo(o) -> bool:
        # pacote com mais de um pedido: o Bling só pode ser cancelado se todos foram cancelados
        if not o.get("pack_id"):
            return False
        st, pack = ml.get(f"/packs/{o['pack_id']}")
        if st != 200:
            return False
        for outro in pack.get("orders") or []:
            if outro.get("id") == o["id"]:
                continue
            st2, od = ml.get(f"/orders/{outro['id']}")
            if st2 == 200 and od.get("status") != "cancelled":
                return True
        return False

    def um(o):
        p = bling_por_loja.get(str(o["id"])) or (bling_por_loja.get(str(o["pack_id"])) if o.get("pack_id") else None)
        if not p or p["situacao"]["id"] == SITUACAO_CANCELADO:
            return None
        sh = (o.get("shipping") or {}).get("id")
        if sh:
            st, envio = ml.get(f"/shipments/{sh}")
            if st == 200 and CANAIS.get(envio.get("logistic_type")) is None:
                return None  # Full (ou outro envio que não passa pelo galpão)
        itens = []
        for it in o.get("order_items") or []:
            i = it.get("item") or {}
            itens.append({"mlb": i.get("id"), "titulo": i.get("title"),
                          "sku": i.get("seller_sku") or i.get("seller_custom_field"), "qtd": it.get("quantity")})
        parcial = tem_outro_ativo(o)
        sit = p["situacao"]["id"]
        return {"conta": conta, "conta_bling": PARES[conta], "pedido": o["id"], "pacote": o.get("pack_id"),
                "cancelado_em": o.get("date_closed") or o.get("last_updated"),
                "bling_id": p["id"], "bling_numero": p.get("numero"),
                "bling_situacao_id": sit, "bling_situacao": SITUACOES.get(sit, str(sit)),
                "bling_url": BLING_VENDA_URL.format(id=p["id"]),
                "acao": "ajustar os itens do pedido no Bling (o pacote tem outro pedido ativo)" if parcial
                        else ("cancelar a NF e o pedido no Bling" if sit == 9 else "cancelar o pedido no Bling"),
                "parcial": parcial, "itens": itens}

    with ThreadPoolExecutor(max_workers=4) as ex:
        return [x for x in ex.map(um, cancelados) if x]


def pendentes(dias: int = DIAS, forcar: bool = False) -> dict:
    chave = dias
    agora = time.time()
    if not forcar and chave in _cache and agora - _cache[chave][0] < CACHE_SEG:
        return _cache[chave][1]
    itens, erros = [], {}
    for c in PARES:
        try:
            itens += _conta(c, dias)
        except Exception as e:
            erros[c] = ("Bling indisponível nesta instalação" if isinstance(e, FileNotFoundError) or "bling_config" in str(e)
                        else f"{type(e).__name__}: {str(e)[:100]}")
    itens.sort(key=lambda x: (x["conta"], x["cancelado_em"] or ""))
    res = {"gerado_em": datetime.now(BRT).isoformat(timespec="seconds"), "dias": dias, "pendentes": itens, "erros": erros}
    if not erros:
        _cache[chave] = (agora, res)
    return res
