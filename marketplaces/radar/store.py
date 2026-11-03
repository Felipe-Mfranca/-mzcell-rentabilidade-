"""
marketplaces/radar/store.py — Dados do Radar ML (lista de acompanhamento, resultados e fila) com sincronização
MZ App

Quem roda o robô (uma máquina só) grava os resultados aqui; as demais máquinas só leem. O compartilhamento usa o
banco que o app já tem (shared_store.py → planilha Google), em três coleções:

  radar_watch   lista de acompanhamento      chave "conta|MLB|palavra-chave"   (qualquer máquina edita)
  radar         último resultado + histórico  mesma chave                       (só o robô grava)
  radar_status  "pedido" (atualizar agora, qualquer máquina) e "execucao" (estado do robô)

Arquivo local: radar_ml.json (fora do GitHub). Conflito: vale o registro com `atualizado_em` mais recente.
"""

import json
import os
import threading
from datetime import datetime, timezone

from marketplaces.radar import parse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DADOS_PATH = os.path.join(BASE_DIR, "radar_ml.json")
HIST_MAX = 90                 # ~30 dias a 3 verificações por dia
TRAVA_EXECUCAO_SEG = 80 * 60  # execução "rodando" há mais que isso é considerada travada
_trava = threading.RLock()


class ErroRadar(Exception):
    pass


def agora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _vazio() -> dict:
    return {"watch": {}, "resultados": {}, "status": {}}


def _carregar() -> dict:
    try:
        with open(DADOS_PATH, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError):
        d = {}
    for k, v in _vazio().items():
        d.setdefault(k, v)
    return d


def _salvar(d: dict):
    tmp = DADOS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, DADOS_PATH)


def _publicar(colecao: str, itens: list, autor: str = ""):
    """[(chave, dados)] para a planilha, sem travar quem chamou. Sem planilha configurada, fica só local."""
    if not itens:
        return
    try:
        import shared_store
        shared_store.enviar_lote_async(colecao, itens, autor)
    except Exception:
        pass


def chave(conta: str, mlb: str, keyword: str) -> str:
    return f"{conta}|{parse.normalizar_mlb(mlb)}|{parse.normalizar_keyword(keyword)}"


# ─── lista de acompanhamento ─────────────────────────────────────────────────

def listar_watch(conta: str = None, so_ativos: bool = False) -> list:
    itens = _carregar()["watch"].values()
    return sorted((w for w in itens if (not conta or w["conta"] == conta) and (not so_ativos or w.get("ativo", True))),
                  key=lambda w: (w["conta"], w["mlb"], w["keyword"]))


def salvar_watch(conta: str, mlb: str, keyword: str, titulo: str = None, sku: str = None, ativo: bool = None,
                 principal: bool = None, autor: str = "") -> dict:
    mlb_n, kw = parse.normalizar_mlb(mlb), parse.normalizar_keyword(keyword)
    if not conta or not mlb_n or not kw:
        raise ErroRadar("conta, MLB e palavra-chave são obrigatórios")
    if len(kw) > 80:
        raise ErroRadar("palavra-chave muito longa (máximo 80 caracteres)")
    with _trava:
        d = _carregar()
        k = chave(conta, mlb_n, kw)
        w = d["watch"].get(k) or {"conta": conta, "mlb": mlb_n, "keyword": kw, "ativo": True, "titulo": "", "sku": "",
                                  "principal": not any(x["conta"] == conta and x["mlb"] == mlb_n for x in d["watch"].values())}
        for campo, valor in (("titulo", titulo), ("sku", sku), ("ativo", ativo), ("principal", principal)):
            if valor is not None:
                w[campo] = bool(valor) if campo in ("ativo", "principal") else str(valor).strip()[:200]
        if not w["titulo"]:       # novo termo do mesmo anúncio herda o título já conhecido
            w["titulo"] = next((x["titulo"] for x in d["watch"].values()
                                if x["conta"] == conta and x["mlb"] == mlb_n and x.get("titulo")), "")
        w["atualizado_em"] = agora_iso()
        d["watch"][k] = w
        publicar = [(k, w)]
        if w["principal"]:        # só um termo principal por anúncio: é o que o modal mostra
            for k2, w2 in d["watch"].items():
                if k2 != k and w2["conta"] == conta and w2["mlb"] == mlb_n and w2.get("principal"):
                    w2["principal"], w2["atualizado_em"] = False, agora_iso()
                    publicar.append((k2, w2))
        _salvar(d)
    _publicar("radar_watch", publicar, autor)
    return w


def remover_watch(conta: str, mlb: str, keyword: str, autor: str = "") -> bool:
    k = chave(conta, mlb, keyword)
    with _trava:
        d = _carregar()
        existia = d["watch"].pop(k, None)
        if existia:
            _salvar(d)
    if existia:   # o histórico continua; a remoção vai à planilha como `removido`
        try:
            import shared_store
            shared_store.enviar_async("radar_watch", k, existia, autor, removido=True)
        except Exception:
            pass
    return bool(existia)


# ─── resultados ──────────────────────────────────────────────────────────────

def registrar_resultados(itens: list, origem: str = "robo") -> list:
    """Cada item: {conta, mlb, keyword, encontrado, pagina, posicao, pagina_pos, ad_type, url, max_pagina, viewer}."""
    publicar, gravados = [], []
    with _trava:
        d = _carregar()
        for it in itens:
            k = chave(it["conta"], it["mlb"], it["keyword"])
            r = d["resultados"].get(k) or {"conta": it["conta"], "mlb": parse.normalizar_mlb(it["mlb"]),
                                           "keyword": parse.normalizar_keyword(it["keyword"]), "historico": []}
            em = agora_iso()
            ok = bool(it.get("encontrado"))
            anterior = r.get("posicao")
            nova = it.get("posicao") if ok else None
            r.update({"pagina": it.get("pagina") if ok else None, "posicao": nova,
                      "pagina_pos": it.get("pagina_pos") if ok else None, "ad_type": it.get("ad_type") if ok else None,
                      "url": it.get("url") if ok else r.get("url"), "max_pagina": it.get("max_pagina"),
                      "viewer": it.get("viewer", ""), "verificado_em": em, "atualizado_em": em,
                      "variacao": parse.variacao(nova, anterior)})
            r["historico"] = (r["historico"] + [{"em": em, "posicao": r["posicao"], "pagina": r["pagina"],
                                                  "pagina_pos": r["pagina_pos"], "ad_type": r["ad_type"],
                                                  "origem": origem}])[-HIST_MAX:]
            d["resultados"][k] = r
            publicar.append((k, r))
            gravados.append(r)
        _salvar(d)
    _publicar("radar", publicar, "robo")
    return gravados


def listar_resultados(conta: str = None) -> list:
    return sorted((r for r in _carregar()["resultados"].values() if not conta or r["conta"] == conta),
                  key=lambda r: (r["conta"], r["mlb"], r["keyword"]))


def resumo_produto(conta: str, mlb: str) -> dict:
    """O que o modal mostra: o termo principal do anúncio (página e posição) e a lista de todos os termos."""
    mlb = parse.normalizar_mlb(mlb)
    d = _carregar()
    watch = [w for w in d["watch"].values() if w["conta"] == conta and w["mlb"] == mlb]
    res = {r["keyword"]: r for r in d["resultados"].values() if r["conta"] == conta and r["mlb"] == mlb}
    principal = next((w for w in watch if w.get("principal")), None) or (watch[0] if watch else None)
    return {"monitorado": bool(watch), "principal": res.get(principal["keyword"]) if principal else None,
            "keyword_principal": principal["keyword"] if principal else "",
            "termos": [{**w, "resultado": res.get(w["keyword"])} for w in watch]}


# ─── fila e estado do robô ───────────────────────────────────────────────────

def _idade_seg(iso: str) -> float:
    try:
        t = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 1e12
    return (datetime.now(timezone.utc) - t).total_seconds()


def status() -> dict:
    return _carregar()["status"]


def estado() -> str:
    """'running' (robô executando), 'queued' (pedido esperando o robô) ou 'idle'."""
    st = status()
    ex, ped = st.get("execucao") or {}, st.get("pedido") or {}
    if ex.get("em_andamento") and _idade_seg(ex.get("iniciou_em")) < TRAVA_EXECUCAO_SEG:
        return "running"
    if ped.get("pedido_em") and ped["pedido_em"] > (ex.get("pedido_atendido") or ""):
        return "queued"
    return "idle"


def _gravar_status(chave_st: str, campos: dict, autor: str = ""):
    with _trava:
        d = _carregar()
        atual = d["status"].get(chave_st) or {}
        atual.update(campos)
        atual["atualizado_em"] = agora_iso()
        d["status"][chave_st] = atual
        _salvar(d)
    _publicar("radar_status", [(chave_st, atual)], autor)
    return atual


def pedir_verificacao(escopo: str = "todas", mlb: str = None, keyword: str = None, autor: str = "") -> dict:
    """'Atualizar agora': qualquer máquina pede; o robô atende na próxima leitura da fila."""
    if estado() != "idle":
        raise ErroRadar("já existe uma verificação na fila ou em andamento")
    return _gravar_status("pedido", {"escopo": escopo or "todas", "mlb": parse.normalizar_mlb(mlb),
                                     "keyword": parse.normalizar_keyword(keyword), "pedido_em": agora_iso(),
                                     "por": autor[:40]}, autor)


def iniciar_execucao(pedido_atendido: str = "") -> bool:
    """Trava: False se já há execução rodando há menos de 80 min."""
    with _trava:
        ex = status().get("execucao") or {}
        if ex.get("em_andamento") and _idade_seg(ex.get("iniciou_em")) < TRAVA_EXECUCAO_SEG:
            return False
        campos = {"em_andamento": True, "iniciou_em": agora_iso(), "erro": ""}
        if pedido_atendido:
            campos["pedido_atendido"] = pedido_atendido
        _gravar_status("execucao", campos, "robo")
    return True


def finalizar_execucao(resumo: str, erro: str = "", mudancas: list = None):
    _gravar_status("execucao", {"em_andamento": False, "terminou_em": agora_iso(), "resumo": resumo, "erro": erro,
                                "mudancas": (mudancas or [])[:30]}, "robo")


def marcar_agendada(rotulo: str):
    _gravar_status("execucao", {"agendado_ultimo": rotulo}, "robo")


# ─── aplicadores (o que chega da planilha) ───────────────────────────────────

def _aplicar(registros: list, secao: str):
    with _trava:
        d = _carregar()
        for r in registros:
            try:
                dados = json.loads(r["dados"]) if isinstance(r["dados"], str) else r["dados"]
            except (ValueError, KeyError):
                continue
            k = r["chave"]
            alvo = d[secao]
            if r.get("removido"):
                alvo.pop(k, None)
                continue
            local = alvo.get(k)
            if local and (local.get("atualizado_em") or "") > (dados.get("atualizado_em") or ""):
                continue            # o local é mais recente
            alvo[k] = dados
        _salvar(d)


def aplicar_watch(registros: list):
    _aplicar(registros, "watch")


def aplicar_resultados(registros: list):
    _aplicar(registros, "resultados")


def aplicar_status(registros: list):
    _aplicar(registros, "status")


APLICADORES = {"radar_watch": aplicar_watch, "radar": aplicar_resultados, "radar_status": aplicar_status}
