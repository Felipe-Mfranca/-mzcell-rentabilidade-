"""
marketplaces/expedicao/snapshot.py — Instantâneo dos pedidos reais para o painel (Monitor TV)
MZ App

Montar os pedidos reais (Mercado Livre das 3 contas + situação no Bling + Amazon DBA + Shopee pelo Bling) leva dezenas de segundos.
A tela do painel não pode esperar isso a cada abertura: este módulo guarda o último resultado, devolve na hora e
renova em segundo plano (a cada ATUALIZA_SEG). Só a primeira carga depois de ligar o servidor demora; para evitar
até isso, o último instantâneo é gravado em disco (expedicao_snapshot.json, fora do git) e reaproveitado.
Somente leitura: nada é escrito em ML, Bling ou Amazon.
"""

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from marketplaces.expedicao import amazon_dba, bling_checkout, ml, shopee_bling

BRT = timezone(timedelta(hours=-3))
ATUALIZA_SEG = 30            # idade a partir da qual uma abertura da tela dispara nova leitura
VALE_DISCO_SEG = 6 * 3600    # instantâneo em disco mais velho que isso não é usado
CAMINHO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "expedicao_snapshot.json")
_trava = threading.Lock()
_estado = {"dados": None, "ts": 0.0, "erro": None, "carregando": False}


def _ler_disco():
    try:
        with open(CAMINHO, encoding="utf-8") as f:
            d = json.load(f)
        if time.time() - d["ts"] < VALE_DISCO_SEG:
            return d
    except (OSError, ValueError, KeyError):
        pass
    return None


def _gravar_disco():
    try:
        with open(CAMINHO + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"ts": _estado["ts"], "dados": _estado["dados"]}, f, ensure_ascii=False, default=str)
        os.replace(CAMINHO + ".tmp", CAMINHO)
    except OSError:
        pass


def montar() -> dict:
    """Leitura completa (lenta). Devolve {linhas, erros}."""
    res = ml.mapa(None, forcar=True)
    linhas, erros_bling = bling_checkout.enriquecer(res["linhas"])
    linhas_dba, erros_dba = amazon_dba.linhas()
    linhas_shopee, erros_shopee = shopee_bling.linhas()
    erros = dict(res.get("erros", {}), **erros_dba, **erros_shopee)
    for c, e in (erros_bling or {}).items():
        erros.setdefault(f"bling:{c}", e)
    return {"linhas": linhas + linhas_dba + linhas_shopee, "erros": erros}


def _atualizar(leitor=None):
    try:
        dados = (leitor or montar)()
        with _trava:
            if dados["erros"] and _estado["dados"] is not None and not dados["linhas"]:
                _estado["erro"] = "; ".join(f"{k}: {v}" for k, v in dados["erros"].items())[:300]   # falhou tudo: mantém o último bom
            else:
                _estado.update(dados=dados, ts=time.time(), erro=None)
                _gravar_disco()
    except Exception as e:
        with _trava:
            _estado["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        with _trava:
            _estado["carregando"] = False


def obter(forcar: bool = False, leitor=None) -> dict:
    """Instantâneo atual, sem esperar. Dispara a renovação em segundo plano quando está velho (ou `forcar`)."""
    with _trava:
        if _estado["dados"] is None:
            d = _ler_disco()
            if d:
                _estado.update(dados=d["dados"], ts=d["ts"])
        velho = time.time() - _estado["ts"] >= ATUALIZA_SEG
        if (velho or forcar) and not _estado["carregando"]:
            _estado["carregando"] = True
            threading.Thread(target=_atualizar, args=(leitor,), daemon=True).start()
        ts = _estado["ts"]
        return {"dados": _estado["dados"], "erro": _estado["erro"], "carregando": _estado["carregando"],
                "iso": datetime.fromtimestamp(ts, BRT).isoformat(timespec="seconds") if ts else None,
                "idade": round(time.time() - ts) if ts else None}
