"""
marketplaces/balanco/catalogo.py — Catálogo dos três Blings para o balanço (somente leitura)
MZ App

Lê, em segundo plano, a categoria dos produtos físicos com saldo e a composição dos kits (clones e kits são
composições). O estoquista conta só o SKU físico; o kit aparece apenas como "compõe". Guarda em balanco_catalogo.json
(gitignored) e renova depois de 24 h.
"""

import json
import os
import threading
import time
from datetime import datetime

from marketplaces.bling import api, estoque

CAMINHO = os.path.join(api.BASE_DIR, "balanco_catalogo.json")
VALIDADE_SEG = 24 * 3600
_estado = {"lendo": False, "feito": 0, "total": 0, "erro": None, "inicio": None}
_trava = threading.Lock()


def _listar(b: str) -> dict:
    prods = {}
    for pg in range(1, 200):
        st, r = api.get(b, "/produtos", pagina=pg, limite=100, criterio=1)
        if st != 200:
            raise RuntimeError(f"{b}: produtos HTTP {st}")
        dados = r.get("data", [])
        for p in dados:
            k = estoque.chave(p.get("codigo"))
            if k:
                prods[p["id"]] = {"sku": k, "nome": p.get("nome") or "", "formato": p.get("formato"), "ativo": p.get("situacao") == "A"}
        if len(dados) < 100:
            break
    return prods


def _categorias(b: str) -> dict:
    st, r = api.get(b, "/categorias/produtos", limite=100)
    if st != 200:
        raise RuntimeError(f"{b}: categorias HTTP {st}")
    d = {c["id"]: c for c in r.get("data", [])}
    def nome(i):
        c = d.get(i)
        if not c:
            return ""
        pai = (c.get("categoriaPai") or {}).get("id")
        return (nome(pai) + " > " if pai else "") + (c.get("descricao") or "")
    return {i: nome(i) for i in d}


def _bling(b: str) -> dict:
    prods, cats = _listar(b), _categorias(b)
    saldos = estoque.saldos(b)["saldos"]
    fisicos = [i for i, p in prods.items() if p["formato"] == "S" and saldos.get(p["sku"])]
    kits = [i for i, p in prods.items() if p["formato"] == "E" and p["ativo"]]
    _estado["total"] += len(fisicos) + len(kits)
    sku_do_id = {i: p["sku"] for i, p in prods.items()}
    saida = {"skus": {}, "kits": {}}
    for i in fisicos:
        st, r = api.get(b, f"/produtos/{i}")
        d = (r or {}).get("data", {}) if st == 200 and isinstance(r, dict) else {}
        saida["skus"][prods[i]["sku"]] = {"nome": prods[i]["nome"], "categoria": cats.get((d.get("categoria") or {}).get("id"), "")}
        _estado["feito"] += 1
    for i in kits:
        st, r = api.get(b, f"/produtos/{i}")
        d = (r or {}).get("data", {}) if st == 200 and isinstance(r, dict) else {}
        est = d.get("estrutura") or {}
        comp = [(sku_do_id.get(c["produto"]["id"]) or str(c["produto"]["id"]), c.get("quantidade") or 1) for c in est.get("componentes") or []]
        saida["kits"][prods[i]["sku"]] = {"tipo": est.get("tipoEstoque"), "comp": comp}
        _estado["feito"] += 1
    return saida


def _ler():
    try:
        res, erros = {}, {}
        def um(b):
            try:
                res[b] = _bling(b)
            except Exception as e:
                erros[b] = f"{type(e).__name__}: {str(e)[:120]}"
        ts = [threading.Thread(target=um, args=(b,)) for b in estoque.BLINGS]
        [t.start() for t in ts]; [t.join() for t in ts]
        if erros:
            _estado["erro"] = erros
        if res:
            with open(CAMINHO, "w", encoding="utf-8") as f:
                json.dump({"gerado_em": datetime.now().isoformat(timespec="seconds"), "ts": time.time(), "blings": res, "erros": erros}, f, ensure_ascii=False)
    finally:
        _estado["lendo"] = False


def atualizar(forcar: bool = False) -> dict:
    """Dispara a leitura em segundo plano (se o arquivo tem mais de 24 h ou se forçado) e devolve o estado."""
    with _trava:
        if not _estado["lendo"] and (forcar or not os.path.isfile(CAMINHO) or time.time() - ler().get("ts", 0) > VALIDADE_SEG):
            _estado.update(lendo=True, feito=0, total=0, erro=None, inicio=time.time())
            threading.Thread(target=_ler, daemon=True).start()
    return dict(_estado)


def ler() -> dict:
    try:
        with open(CAMINHO, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}
