"""
marketplaces/compras/previsao.py — Previsão de compras e reposição do Full (somente leitura)
MZ App

Processo:
1. Demanda diária de cada estoque do Full = 40% média dos últimos 7 dias + 60% média dos últimos 30 (reposicao_full.calcular).
2. Meta: ao menos META_DIAS (20) de cobertura. Abaixo disso, envio ao Full = META_DIAS x demanda - estoque do Full.
3. Saldo disponível no Bling (depósito Geral, físico menos as reservas de pedidos em aberto). O Bling da conta é usado primeiro; se faltar, recomenda-se empréstimo dos outros Blings.
4. Se ainda faltar, ou se após o envio sobrar menos que SOBRA_PCT do que foi enviado, entra no pedido de compra.
Kits e clones são composições: o saldo vem do menor múltiplo dos componentes e a compra é expandida para eles.
A demanda é somada por SKU antes de comprar, porque o estoque físico é um só para as três contas.
"""

import json
import math
import os
import threading
import time
from datetime import datetime, timedelta

import ml_api
import reposicao_full
from marketplaces.balanco import catalogo
from marketplaces.bling import estoque, skus_filho

META_DIAS = 20
SOBRA_PCT = 0.20
PESOS = {7: 0.4, 30: 0.6}
CONTAS = ["meli01", "meli02", "meli03"]
VALIDADE_SEG = 6 * 3600
CAMINHO = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "compras_cache.json")
_estado = {"lendo": False, "etapa": "", "erro": None}
_trava = threading.Lock()


def _ler_cache() -> dict:
    try:
        with open(CAMINHO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ler_demanda():
    """Linhas do Full das 3 contas (demora alguns minutos: vendas dia a dia e estoque por inventário)."""
    ate = (datetime.now(reposicao_full.ML_TZ) - timedelta(days=1)).date()
    linhas, erros = [], {}
    for c in CONTAS:
        _estado["etapa"] = f"Full {c}"
        try:
            linhas += reposicao_full.calcular(c, ate, PESOS)
        except Exception as e:
            erros[c] = f"{type(e).__name__}: {str(e)[:120]}"
    return linhas, erros


def _saldo_sku(sku, b, saldos, kits, cache=None):
    """Disponível do SKU no Bling b. Kit/clone com estoque virtual: menor múltiplo dos componentes."""
    k = kits.get(b, {}).get(sku)
    if k and k.get("tipo") != "F" and k.get("comp"):
        return min((max(_saldo_sku(c, b, saldos, {}), 0) // max(q, 1)) for c, q in k["comp"])
    return max(saldos.get(b, {}).get(sku) or 0, 0)


def _montar(linhas: list) -> dict:
    saldos = {b: estoque.saldos(b)["virtuais"] for b in estoque.BLINGS}   # disponível = físico - reservas de pedidos em aberto
    kits = {b: d.get("kits", {}) for b, d in (catalogo.ler().get("blings") or {}).items()}
    filhos = {c: skus_filho.por_conta(c) for c in CONTAS}
    for l in linhas:
        sku = filhos.get(l["conta"], {}).get(l["mlb"]) or l.get("sku") or ""
        l["sku_bling"] = estoque.chave(sku)
    # só quem precisa enviar entra no cálculo de saldo
    envios = [l for l in linhas if l["sugestao_reposicao"] > 0]
    envios.sort(key=lambda l: (l["cobertura_dias"] is None, l["cobertura_dias"] or 0))
    por_sku = {}
    for l in envios:
        por_sku.setdefault(l["sku_bling"], []).append(l)
    compras, emprestimos = [], []
    for sku, ls in por_sku.items():
        if not sku:   # anúncio sem SKU: não dá para cruzar com o Bling (vai para "sem SKU")
            for l in ls:
                l.update(do_bling_proprio=0, bling_proprio=estoque.BLING_DA_CONTA[l["conta"]], emprestimo={}, sem_saldo=l["sugestao_reposicao"])
            continue
        pools ={b: (_saldo_sku(sku, b, saldos, kits) if sku else 0) for b in estoque.BLINGS}
        total_envio = sum(l["sugestao_reposicao"] for l in ls)
        sobra_ini = sum(pools.values())
        for l in ls:
            falta = l["sugestao_reposicao"]
            proprio = estoque.BLING_DA_CONTA[l["conta"]]
            tirar = min(falta, pools[proprio]); pools[proprio] -= tirar; falta -= tirar
            l["do_bling_proprio"] = tirar; l["bling_proprio"] = proprio
            emp = {}
            for b in estoque.BLINGS:
                if falta <= 0:
                    break
                if b == proprio:
                    continue
                t = min(falta, pools[b]); pools[b] -= t; falta -= t
                if t:
                    emp[b] = t
                    emprestimos.append({"sku": sku, "de": b, "para": proprio, "qtd": t, "conta": l["conta"], "mlb": l["mlb"]})
            l["emprestimo"] = emp; l["sem_saldo"] = falta
        atendido = total_envio - sum(l["sem_saldo"] for l in ls)
        sobra = sobra_ini - atendido
        faltante = sum(l["sem_saldo"] for l in ls)
        # compra = o que falta + o que completa a sobra mínima (SOBRA_PCT do que vai para o Full) depois de atender os envios
        compra = faltante + max(0, math.ceil(SOBRA_PCT * total_envio) - sobra)
        if compra > 0:
            comp = kits.get(estoque.BLING_DA_CONTA[ls[0]["conta"]], {}).get(sku)
            compras.append({"sku": sku, "titulo": ls[0]["titulo"], "envio_total": total_envio, "saldo_blings": sobra_ini,
                            "faltante": faltante, "compra": compra, "contas": sorted({l["conta"] for l in ls}),
                            "componentes": [{"sku": c, "qtd": q, "compra": q * compra} for c, q in comp["comp"]] if comp and comp.get("tipo") != "F" else []})
    compras.sort(key=lambda c: -c["compra"])
    return {"envios": envios, "compras": compras, "emprestimos": emprestimos,
            "sem_sku": [l for l in linhas if not l["sku_bling"] and l["sugestao_reposicao"] > 0]}


def _rodar():
    try:
        linhas, erros = _ler_demanda()
        _estado["etapa"] = "Cruzando com o saldo do Bling"
        res = _montar(linhas)
        res.update(gerado_em=datetime.now().isoformat(timespec="seconds"), ts=time.time(), erros=erros,
                   parametros={"meta_dias": META_DIAS, "sobra_pct": SOBRA_PCT, "pesos": {str(k): v for k, v in PESOS.items()}},
                   total_inventarios=len(linhas))
        with open(CAMINHO, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, default=str)
    except Exception as e:
        _estado["erro"] = f"{type(e).__name__}: {str(e)[:200]}"
    finally:
        _estado["lendo"] = False
        _estado["etapa"] = ""


def dados(forcar: bool = False) -> dict:
    c = _ler_cache()
    with _trava:
        if not _estado["lendo"] and (forcar or not c or time.time() - c.get("ts", 0) > VALIDADE_SEG):
            _estado.update(lendo=True, erro=None, etapa="Iniciando")
            threading.Thread(target=_rodar, daemon=True).start()
    return dict(c, estado=dict(_estado))
