"""
marketplaces/expedicao/bling_checkout.py — Situação do pedido no Bling para cada envio da Expedição (somente leitura)
MZ App

Verificado (situação 24) = checkout feito pelo estoquista. Pedido verificado já saiu da montagem: o tempo de
embalagem não conta mais e o envio fica "aguardando coleta ou postagem".
O Bling guarda o número do pedido ou o do pacote do ML em numeroLoja; casa pelos dois.
Se o Bling não responder, os envios seguem sem a informação (valem as marcas manuais do galpão).
"""

import time
from datetime import datetime, timedelta

from marketplaces.bling import api as bling
from marketplaces.expedicao.ml import BRT, DIAS_BUSCA

PARES = {"meli01": "bling01", "meli02": "bling02", "meli03": "bling03"}
SITUACAO_VERIFICADO = 24
SITUACOES = {6: "Em aberto", 9: "Atendido", 12: "Cancelado", 15: "Em andamento", 21: "Em digitação",
             24: "Verificado", 753338: "Pagamento aprovado", 753339: "Em devolução"}
_cache = {}
CACHE_SEG = 60


def _pedidos(conta_bling: str) -> dict:
    agora = time.time()
    if conta_bling in _cache and agora - _cache[conta_bling][0] < CACHE_SEG:
        return _cache[conta_bling][1]
    de = (datetime.now(BRT) - timedelta(days=DIAS_BUSCA + 2)).strftime("%Y-%m-%d")
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
    _cache[conta_bling] = (agora, por_loja)
    return por_loja


def enriquecer(linhas: list) -> tuple:
    """Devolve (linhas com os campos do Bling, erros por conta). Não altera a lista recebida."""
    erros, por_conta = {}, {}
    for conta in {l["conta"] for l in linhas}:
        try:
            por_conta[conta] = _pedidos(PARES[conta])
        except Exception as e:
            erros[conta] = f"Bling: {type(e).__name__}: {str(e)[:100]}"
    saida = []
    for l in linhas:
        nova = dict(l, bling_numero=None, bling_situacao=None, bling_checkout=False)
        loja = por_conta.get(l["conta"])
        if loja is not None:
            p = loja.get(str(l["pedido"])) or (loja.get(str(l["pacote"])) if l.get("pacote") else None)
            if p:
                sit = p["situacao"]["id"]
                nova.update(bling_numero=p.get("numero"), bling_situacao=SITUACOES.get(sit, str(sit)),
                            bling_checkout=sit == SITUACAO_VERIFICADO)
        saida.append(nova)
    return saida, erros
