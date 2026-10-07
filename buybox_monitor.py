"""
buybox_monitor.py — Monitor de buybox dos anúncios de catálogo (SOMENTE LEITURA)
MZCell Rentabilidade

Módulo independente: não é importado pelo main.py nem pelo sync_service.py.
Nenhuma ação é executada no Mercado Livre — só consultas (GET).
Escreve apenas buybox_state.json (e config.json via ml_api.refresh_token
quando um token expira, igual ao app).

Uso:
  python buybox_monitor.py                         # todas as contas, só mostra
  python buybox_monitor.py --contas meli03         # só meli03
  python buybox_monitor.py --contas meli03 --enviar  # envia ao Slack
  python buybox_monitor.py --loop                  # roda nos horários do slack_config.json
"""

import argparse
import asyncio
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import httpx

import ml_api

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(BASE_DIR, "buybox_state.json")
SLACK_PATH = os.path.join(BASE_DIR, "slack_config.json")
PRODUTOS_PATH = os.path.join(BASE_DIR, "produtos.json")
BRT = timezone(timedelta(hours=-3))

STATUS_PT = {
    "winning": "ganhando",
    "sharing_first_place": "dividindo 1º lugar",
    "competing": "perdendo",
    "listed": "perdendo",
    "not_listed": "fora da disputa",
}


# ─── HTTP ────────────────────────────────────────────────────────────────────

class ContaSemAcesso(Exception):
    pass


class ClienteML:
    """GET com renovação de token em 401 e espera crescente em 429/5xx."""

    def __init__(self, conta: str):
        self.conta = conta
        self.cli = httpx.Client(timeout=30, verify=False)
        self.renovou = False

    def _headers(self):
        return {"Authorization": f"Bearer {ml_api.get_token(self.conta)}"}

    def get(self, path: str, **params):
        espera = 1
        for _ in range(6):
            r = self.cli.get(ml_api.ML_BASE_URL + path, params=params, headers=self._headers())
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
            if r.status_code == 401 and not self.renovou:
                self.renovou = True
                res = asyncio.run(ml_api.refresh_token(self.conta))
                if not res.get("success"):
                    raise ContaSemAcesso(f"{self.conta}: token inválido e renovação falhou")
                continue
            if r.status_code in (401, 403):
                raise ContaSemAcesso(f"{self.conta}: acesso negado ({r.status_code}) em {path}")
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(espera)
                espera = min(espera * 2, 30)
                continue
            raise RuntimeError(f"{r.status_code} em {path}: {r.text[:200]}")
        raise RuntimeError(f"Desisti após várias tentativas em {path}")


# ─── COLETA ──────────────────────────────────────────────────────────────────

def anuncios_catalogo(ml: ClienteML, seller_id: str) -> list:
    ids, offset = [], 0
    while True:
        b = ml.get(f"/users/{seller_id}/items/search",
                   status="active", catalog_listing="true", limit=100, offset=offset)
        res = (b or {}).get("results", [])
        ids += res
        offset += len(res)
        if not res or offset >= b["paging"]["total"] or offset >= 1000:
            break
    itens = []
    for i in range(0, len(ids), 20):
        lote = ml.get("/items", ids=",".join(ids[i:i + 20]),
                      attributes="id,title,status,price,catalog_product_id,shipping") or []
        itens += [e["body"] for e in lote if e.get("code") == 200]
    return itens


def nickname(ml: ClienteML, seller_id, cache: dict) -> str:
    k = str(seller_id)
    if k not in cache:
        u = ml.get(f"/users/{k}")
        cache[k] = (u or {}).get("nickname") or k
    return cache[k]


def piso_margem(prod: dict):
    """Preço com margem zero: (CMV + frete) / (1 − imposto − comissão)."""
    if not prod or not prod.get("cmv_unit"):
        return None
    taxa = (prod.get("imposto_pct") or 0) + (prod.get("comissao_pct") or 0)
    if taxa >= 1:
        return None
    return round(((prod.get("cmv_unit") or 0) + (prod.get("frete_unit") or 0)) / (1 - taxa), 2)


def varrer(contas: list) -> dict:
    cfg = ml_api.load_config()["contas"]
    nossos = {str(v.get("seller_id")): k for k, v in cfg.items() if v.get("seller_id")}
    try:
        produtos = json.load(open(PRODUTOS_PATH, encoding="utf-8"))
    except Exception:
        produtos = {}
    estado_ant = carregar_estado()
    nicks = dict(estado_ant.get("nicknames", {}))

    anuncios, catalogos, erros = [], {}, []
    for conta in contas:
        if conta not in cfg:
            erros.append(f"{conta}: conta não existe no config.json")
            continue
        ml = ClienteML(conta)
        try:
            itens = anuncios_catalogo(ml, cfg[conta]["seller_id"])
            for it in itens:
                mlb, pid = it["id"], it.get("catalog_product_id")
                if (produtos.get(mlb) or {}).get("arquivado"):
                    continue
                ptw = ml.get(f"/items/{mlb}/price_to_win", siteId="MLB", version="v2") or {}
                if pid and pid not in catalogos:
                    lista = ml.get(f"/products/{pid}/items") or {}
                    concorrentes = []
                    for r in lista.get("results", []):
                        sh = r.get("shipping") or {}
                        concorrentes.append({
                            "item_id": r["item_id"],
                            "seller_id": str(r["seller_id"]),
                            "seller": nickname(ml, r["seller_id"], nicks),
                            "nossa_conta": nossos.get(str(r["seller_id"])),
                            "preco": r.get("price"),
                            "frete_gratis": bool(sh.get("free_shipping")),
                            "full": sh.get("logistic_type") == "fulfillment",
                        })
                    catalogos[pid] = concorrentes
                vencedor = (ptw.get("winner") or {}).get("item_id")
                anuncios.append({
                    "conta": conta, "mlb": mlb, "catalogo": pid,
                    "titulo": it.get("title", ""),
                    "status": ptw.get("status", "desconhecido"),
                    "motivo": ptw.get("reason") or [],
                    "preco": ptw.get("current_price") or it.get("price"),
                    "price_to_win": ptw.get("price_to_win"),
                    "vencedor_item": vencedor,
                    "vencedor_preco": (ptw.get("winner") or {}).get("price"),
                    "dividindo_com": ptw.get("competitors_sharing_first_place"),
                    "piso": piso_margem(produtos.get(mlb)),
                })
        except ContaSemAcesso as e:
            erros.append(str(e))

    return {"quando": datetime.now(BRT).isoformat(timespec="minutes"),
            "anuncios": anuncios, "catalogos": catalogos, "erros": erros,
            "nicknames": nicks, "nossos": nossos}


# ─── ESTADO / MUDANÇAS ───────────────────────────────────────────────────────

def carregar_estado() -> dict:
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except Exception:
        return {}


def salvar_estado(v: dict):
    estado = {
        "quando": v["quando"],
        "status": {a["mlb"]: a["status"] for a in v["anuncios"]},
        "sellers": {pid: sorted({c["seller_id"] for c in cs}) for pid, cs in v["catalogos"].items()},
        "nicknames": v["nicknames"],
    }
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)


def mudancas(v: dict, ant: dict) -> list:
    if not ant:
        return []
    out = []
    st_ant = ant.get("status", {})
    for a in v["anuncios"]:
        antes = st_ant.get(a["mlb"])
        if antes and antes != a["status"]:
            if antes == "winning":
                out.append(f"🔴 PERDEMOS a buybox: {a['mlb']} ({a['conta']}) — {a['titulo'][:40]}")
            elif a["status"] == "winning":
                out.append(f"🟢 RECUPERAMOS a buybox: {a['mlb']} ({a['conta']}) — {a['titulo'][:40]}")
            else:
                out.append(f"• {a['mlb']}: {STATUS_PT.get(antes, antes)} → {STATUS_PT.get(a['status'], a['status'])}")
    sel_ant = ant.get("sellers", {})
    for pid, cs in v["catalogos"].items():
        if pid not in sel_ant:
            continue
        for c in cs:
            if c["seller_id"] not in sel_ant[pid] and not c["nossa_conta"]:
                out.append(f"🆕 Seller novo em {pid}: {c['seller']} a R$ {c['preco']:.2f}")
    return out


# ─── RELATÓRIO ───────────────────────────────────────────────────────────────

def _r(x):
    return f"R$ {x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") if x is not None else "—"


def montar_relatorio(v: dict, ant: dict) -> str:
    L = [f"*Monitor de Buybox* — {v['quando'][:16].replace('T', ' ')} BRT"]
    cont = Counter(a["status"] for a in v["anuncios"])
    L.append(" | ".join(f"{STATUS_PT.get(k, k)}: {n}" for k, n in cont.most_common()) or "nenhum anúncio de catálogo ativo")

    mud = mudancas(v, ant)
    L.append("\n*Mudanças desde a última varredura*")
    L += mud or (["(primeira varredura — sem base de comparação)"] if not ant else ["nenhuma"])

    ordem = {"competing": 0, "listed": 0, "sharing_first_place": 1, "not_listed": 2, "winning": 3}
    L.append("\n*Anúncios*")
    for a in sorted(v["anuncios"], key=lambda a: (ordem.get(a["status"], 9), a["conta"])):
        st = STATUS_PT.get(a["status"], a["status"])
        linha = f"• [{a['conta']}] {a['mlb']} — {a['titulo'][:45]}\n   {st} | nosso {_r(a['preco'])}"
        if a["status"] not in ("winning", "not_listed"):
            linha += f" | p/ ganhar {_r(a['price_to_win'])} | vencedor {_r(a['vencedor_preco'])}"
        if a["status"] == "sharing_first_place":
            linha += f" | dividindo com {a['dividindo_com']}"
        if a["status"] == "not_listed" and a["motivo"]:
            linha += f" | motivo: {', '.join(a['motivo'])}"
        if a["piso"]:
            linha += f" | piso {_r(a['piso'])}"
            if a["price_to_win"] and a["price_to_win"] < a["piso"]:
                linha += " ⚠️ p/ ganhar está ABAIXO do piso"
        L.append(linha)
        for c in v["catalogos"].get(a["catalogo"], []):
            if c["item_id"] == a["mlb"]:
                continue
            tags = [x for x, ok in (("Full", c["full"]), ("frete grátis", c["frete_gratis"])) if ok]
            quem = f"{c['seller']} (NOSSA {c['nossa_conta']} {c['item_id']})" if c["nossa_conta"] else c["seller"]
            venc = " 🏆" if c["item_id"] == a["vencedor_item"] else ""
            abaixo = " ⚠️<piso" if a["piso"] and c["preco"] and c["preco"] < a["piso"] else ""
            L.append(f"     ↳ {quem}: {_r(c['preco'])} {'· '.join(tags)}{venc}{abaixo}")

    disputa = defaultdict(set)
    for a in v["anuncios"]:
        disputa[a["catalogo"]].add(a["conta"])
    for pid, cs in v["catalogos"].items():
        for c in cs:
            if c["nossa_conta"]:
                disputa[pid].add(c["nossa_conta"])
    dup = {pid: sorted(cs) for pid, cs in disputa.items() if pid and len(cs) > 1}
    L.append("\n*Contas nossas no mesmo catálogo*")
    L += [f"• {pid}: {', '.join(cs)}" for pid, cs in dup.items()] or ["nenhuma"]

    rank = Counter()
    for cs in v["catalogos"].values():
        for s in {c["seller"] for c in cs if not c["nossa_conta"]}:
            rank[s] += 1
    L.append("\n*Sellers que mais aparecem nos nossos catálogos*")
    L += [f"{i}. {s} — {n} catálogo(s)" for i, (s, n) in enumerate(rank.most_common(10), 1)] or ["nenhum concorrente"]

    if v["erros"]:
        L.append("\n*Erros*")
        L += [f"• {e}" for e in v["erros"]]
    return "\n".join(L)


# ─── SLACK ───────────────────────────────────────────────────────────────────

def carregar_slack() -> dict:
    try:
        return json.load(open(SLACK_PATH, encoding="utf-8"))
    except Exception:
        return {}


def enviar_slack(texto: str) -> bool:
    url = carregar_slack().get("webhook_url")
    if not url:
        print("slack_config.json sem webhook_url — não enviado.")
        return False
    r = httpx.post(url, json={"text": texto}, timeout=20)
    print("Slack:", r.status_code, r.text[:100])
    return r.status_code == 200


# ─── EXECUÇÃO ────────────────────────────────────────────────────────────────

def executar(contas: list, enviar: bool) -> str:
    ant = carregar_estado()
    v = varrer(contas)
    texto = montar_relatorio(v, ant)
    salvar_estado(v)
    if enviar:
        enviar_slack(texto)
    return texto


def proximo_horario(horarios: list) -> datetime:
    agora = datetime.now(BRT)
    cands = []
    for h in horarios:
        hh, mm = map(int, h.split(":"))
        t = agora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        cands.append(t if t > agora else t + timedelta(days=1))
    return min(cands)


def loop():
    while True:
        sc = carregar_slack()
        alvo = proximo_horario(sc.get("horarios") or ["08:00", "13:00", "18:00"])
        print(f"Próxima varredura: {alvo:%d/%m %H:%M} BRT")
        time.sleep(max(0, (alvo - datetime.now(BRT)).total_seconds()))
        contas = sc.get("contas") or list(ml_api.load_config()["contas"])
        try:
            executar(contas, enviar=True)
        except Exception as e:
            print("Erro na varredura:", e)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    p = argparse.ArgumentParser(description="Monitor de buybox (somente leitura)")
    p.add_argument("--contas", help="ex.: meli03 ou meli01,meli03 (padrão: todas)")
    p.add_argument("--enviar", action="store_true", help="envia o relatório ao Slack")
    p.add_argument("--loop", action="store_true", help="roda nos horários do slack_config.json")
    a = p.parse_args()
    if a.loop:
        loop()
    else:
        contas = a.contas.split(",") if a.contas else list(ml_api.load_config()["contas"])
        print(executar(contas, a.enviar))
