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
from marketplaces.expedicao.ml import BRT, CANAIS, situacao as situacao_ml
from reposicao_full import ML

PARES = {"meli01": "bling01", "meli02": "bling02", "meli03": "bling03"}
SITUACAO_CANCELADO = 12
SITUACOES = {6: "Em aberto", 9: "Atendido (NF emitida)", 15: "Em andamento", 21: "Em digitação",
             24: "Verificado (checkout feito)", 753338: "Pagamento aprovado", 753339: "Em devolução"}
BLING_VENDA_URL = "https://www.bling.com.br/vendas.php#edit/{id}"
SITUACAO_VERIFICADO = 24
# Pacote que já saiu do galpão: cancelar no Bling NÃO desfaz o envio.
POSTADO_STATUS = ("shipped", "delivered", "not_delivered")
POSTADO_SUBSTATUS = ("in_hub", "in_packing_list", "dropped_off")
DIAS = 7
_cache = {}
CACHE_SEG = 120


def situacao_fisica(status, substatus) -> str:
    """'postado' (já saiu do galpão), 'envio_cancelado' (o ML cancelou o envio) ou 'no_galpao'."""
    if status in POSTADO_STATUS or (status == "ready_to_ship" and substatus in POSTADO_SUBSTATUS):
        return "postado"
    if status == "cancelled":
        return "envio_cancelado"
    return "no_galpao"


def motivo_cancelamento(cancel: dict) -> str:
    """Quem/por que o ML cancelou o pedido (cancel_detail)."""
    g, code = (cancel or {}).get("group"), (cancel or {}).get("code")
    if g == "mediations":
        return "reclamação do comprador (mediação do ML)"
    if code == "shipment_not_delivered":
        return "envio não entregue"
    if g == "buyer":
        return "cancelado pelo comprador"
    return "cancelado pelo ML" + (f" ({code})" if code else "")


def resumo_devolucao(ret: dict) -> dict:
    """Estado da logística reversa a partir de /post-purchase/v2/claims/{id}/returns."""
    envios = (ret or {}).get("shipments") or []
    e = envios[0] if envios else {}
    return {"status": (ret or {}).get("status"), "tipo": (ret or {}).get("subtype"), "dinheiro": (ret or {}).get("status_money"),
            "envio_status": e.get("status"), "rastreio": e.get("tracking_number")}


def acao_e_risco(fisico: str, bling_sit: int, parcial: bool, status=None, substatus=None, devolucao=None) -> tuple:
    """(risco, ação). O risco ordena a lista: postado (0) > verificado (1) > simples (2)."""
    if parcial:
        return "simples", "ajustar os itens do pedido no Bling (o pacote tem outro pedido ativo)"
    if fisico == "postado":
        nf = " Trate também a NF conforme a regra fiscal (devolução ou cancelamento)." if bling_sit == 9 else ""
        if devolucao:
            rastro = f" (rastreio {devolucao['rastreio']})" if devolucao.get("rastreio") else ""
            est = devolucao.get("envio_status") or devolucao.get("status")
            if est == "delivered":
                return "postado", ("DEVOLUÇÃO JÁ CHEGOU pela logística reversa do ML" + rastro + ": conferir o recebimento físico no galpão, dar entrada "
                                   "no estoque se o produto estiver bom e então cancelar no Bling." + nf)
            if est in ("shipped", "out_for_delivery"):
                return "postado", ("DEVOLUÇÃO A CAMINHO" + rastro + ": dar entrada no estoque só quando chegar ao galpão; depois cancelar no Bling." + nf)
            if est in ("cancelled", "failed", "not_delivered"):
                return "postado", ("DEVOLUÇÃO NÃO CONCLUÍDA (" + str(est) + ")" + rastro + ": o produto pode ter ficado com o comprador. Tratar com o SAC "
                                   "(reclamação) antes de qualquer baixa." + nf)
            return "postado", ("DEVOLUÇÃO CRIADA, o comprador ainda não postou" + rastro + ": acompanhar. Sem entrada no estoque por enquanto; "
                               "cancelar no Bling NÃO faz o produto voltar." + nf)
        if status == "delivered":
            return "postado", ("ENTREGUE E SEM DEVOLUÇÃO REGISTRADA no ML: verifique com o SAC se o comprador ficou com o produto antes de dar baixa. "
                               "Cancelar no Bling NÃO traz o produto de volta." + nf)
        if status == "not_delivered" or substatus in ("returned", "returning_to_sender"):
            return "postado", ("ENVIO NÃO ENTREGUE, o pacote está VOLTANDO ao vendedor: dar entrada no estoque só quando ele chegar ao galpão; "
                               "depois cancelar no Bling." + nf)
        return "postado", ("PACOTE JÁ POSTADO (a caminho do comprador): cancelar no Bling NÃO o faz voltar. O ML cuida da logística reversa; "
                           "acompanhe o retorno e dê entrada no estoque só quando chegar ao galpão." + nf)
    if bling_sit == SITUACAO_VERIFICADO:
        return "verificado", ("pedido já VERIFICADO (separado e embalado): localizar o pacote e tirar da expedição, devolver o produto ao estoque "
                              "e só então cancelar no Bling")
    return "simples", "cancelar a NF e o pedido no Bling" if bling_sit == 9 else "cancelar o pedido no Bling"


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
        envio = {}
        if sh:
            st, envio = ml.get(f"/shipments/{sh}")
            if st != 200:
                envio = {}
            elif CANAIS.get(envio.get("logistic_type")) is None:
                return None  # Full (ou outro envio que não passa pelo galpão)
        itens = []
        for it in o.get("order_items") or []:
            i = it.get("item") or {}
            itens.append({"mlb": i.get("id"), "titulo": i.get("title"),
                          "sku": i.get("seller_sku") or i.get("seller_custom_field"), "qtd": it.get("quantity")})
        parcial = tem_outro_ativo(o)
        sit = p["situacao"]["id"]
        fisico = situacao_fisica(envio.get("status"), envio.get("substatus"))
        devolucao, claim_id = None, None
        if fisico == "postado":
            for m in (o.get("mediations") or []):
                claim_id = m.get("id") if isinstance(m, dict) else m
                try:
                    stc, ret = ml.get(f"/post-purchase/v2/claims/{claim_id}/returns")
                    if stc == 200 and isinstance(ret, dict):
                        devolucao = resumo_devolucao(ret)
                        break
                except Exception:
                    pass
        risco, acao = acao_e_risco(fisico, sit, parcial, envio.get("status"), envio.get("substatus"), devolucao)
        return {"conta": conta, "conta_bling": PARES[conta], "pedido": o["id"], "pacote": o.get("pack_id"),
                "cancelado_em": o.get("date_closed") or o.get("last_updated"),
                "bling_id": p["id"], "bling_numero": p.get("numero"),
                "bling_situacao_id": sit, "bling_situacao": SITUACOES.get(sit, str(sit)),
                "bling_url": BLING_VENDA_URL.format(id=p["id"]),
                "ml_envio_status": envio.get("status"), "ml_envio_substatus": envio.get("substatus"),
                "ml_envio_situacao": situacao_ml(envio.get("status"), envio.get("substatus")) if envio else "",
                "fisico": fisico, "risco": risco, "acao": acao, "motivo": motivo_cancelamento(o.get("cancel_detail")),
                "claim_id": claim_id, "devolucao": devolucao,
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
    itens.sort(key=lambda x: ({"postado": 0, "verificado": 1}.get(x["risco"], 2), x["conta"], x["cancelado_em"] or ""))
    res = {"gerado_em": datetime.now(BRT).isoformat(timespec="seconds"), "dias": dias, "pendentes": itens, "erros": erros}
    if not erros:
        _cache[chave] = (time.time(), res)     # hora do fim da leitura
    return res
