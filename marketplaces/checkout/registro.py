"""
marketplaces/checkout/registro.py — Registro das leituras e relatório do modo sombra (nada é gravado no Bling)
MZ App

Cada bipagem vira uma linha em checkout_registro.jsonl (fora do git) com o que o app PREVIU fazer.
Depois de alguns dias, avaliar() cruza a previsão com a situação que o pedido tem hoje no Bling
(o estoquista continua fazendo o checkout à mão): isso mede a assertividade antes de qualquer gravação.
"""

import json
import os
import threading
from datetime import datetime, timedelta, timezone

BRT = timezone(timedelta(hours=-3))
ARQUIVO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "checkout_registro.jsonl")
_trava = threading.Lock()

SIT_ABERTAS = (None, 6, 15, 21, 753338)
SIT_FEITO = (24, 9)          # Verificado, Atendido
SIT_RUIM = (12, 753339)      # Cancelado, Em devolução


def acao_prevista(r: dict) -> str:
    """O que o app faria se gravasse: marcar_verificado ou nenhuma."""
    sit = (r.get("bling") or {}).get("situacao_id")
    if r.get("nivel") == "ok" and r.get("bling") and sit not in SIT_FEITO:
        return "marcar_verificado"
    return "nenhuma"


def gravar(r: dict, lido: str, arquivo: str = None) -> None:
    """Acrescenta a leitura ao registro. Nunca levanta erro: o registro não pode derrubar a bipagem."""
    try:
        b, m = r.get("bling") or {}, r.get("ml") or {}
        linha = {"ts": datetime.now(BRT).isoformat(timespec="seconds"), "lido": str(lido or "")[:60],
                 "conta": r.get("conta"), "bling_conta": r.get("bling_conta"), "envio": m.get("envio"),
                 "pedido": m.get("pedido"), "pacote": m.get("pacote"), "canal": m.get("canal"),
                 "numeroLoja": b.get("numeroLoja"), "bling_numero": b.get("numero"),
                 "situacao_id": b.get("situacao_id"), "nivel": r.get("nivel"),
                 "acao": acao_prevista(r), "rastreio_confere": r.get("rastreio_confere"), "mensagem": r.get("mensagem")}
        with _trava, open(arquivo or ARQUIVO, "a", encoding="utf-8") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
    except Exception:
        pass


def ler(arquivo: str = None) -> list:
    try:
        with open(arquivo or ARQUIVO, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]
    except OSError:
        return []


def _classificar(e: dict, agora) -> tuple:
    """(classe, motivo) para a PRIMEIRA leitura de um pedido, sabendo a situação de agora (None = não achado)."""
    if e.get("bling_numero") is None:
        return "sem_pedido", "O pedido não existia no Bling na hora da leitura."
    if e["acao"] == "marcar_verificado":
        if agora in SIT_FEITO:
            return "confere", "O app previu o checkout e o estoquista fez."
        if agora in SIT_RUIM:
            return "divergente", "O app previu o checkout, mas o pedido acabou cancelado/em devolução."
        return "pendente", "O app previu o checkout e o pedido ainda está em aberto."
    if e.get("nivel") == "bloqueio":
        if agora in SIT_FEITO:
            return "divergente", "O app bloquearia (" + str(e.get("mensagem")) + "), mas o pedido seguiu no Bling."
        return "confere", "O app bloqueou e o pedido não foi despachado."
    if e.get("situacao_id") in SIT_FEITO:
        return "confere", "Já estava com checkout feito na leitura."
    return "sem_avaliacao", str(e.get("mensagem") or "")


def avaliar(entradas: list, situacao_atual) -> dict:
    """situacao_atual(bling_conta, numeroLoja) -> id da situação de hoje ou None. Considera a 1ª leitura de cada pedido."""
    vistos, itens = set(), []
    for e in entradas:
        chave = (e.get("bling_conta"), e.get("numeroLoja") or e.get("envio"))
        if chave in vistos:
            continue
        vistos.add(chave)
        agora = situacao_atual(e.get("bling_conta"), e.get("numeroLoja")) if e.get("numeroLoja") else None
        classe, motivo = _classificar(e, agora)
        itens.append({"classe": classe, "motivo": motivo, "envio": e.get("envio"), "pedido": e.get("pedido"),
                      "bling_numero": e.get("bling_numero"), "conta": e.get("conta"), "canal": e.get("canal"),
                      "primeira_leitura": e.get("ts"), "situacao_agora": agora})
    cont = {}
    for i in itens:
        cont[i["classe"]] = cont.get(i["classe"], 0) + 1
    fechados = cont.get("confere", 0) + cont.get("divergente", 0)
    return {"leituras": len(entradas), "pedidos": len(itens), "contagem": cont,
            "assertividade_pct": round(100 * cont.get("confere", 0) / fechados, 1) if fechados else None,
            "precisam_olhar": [i for i in itens if i["classe"] in ("divergente", "sem_pedido")],
            "pendentes": [i for i in itens if i["classe"] == "pendente"]}
