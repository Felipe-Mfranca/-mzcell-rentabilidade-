"""
diag_full.py — Diagnóstico de endpoints de Fulfillment (Full) para reposição
MZCell Rentabilidade

SOMENTE LEITURA: só faz GET na API do Mercado Livre. Não altera o app.
Uso:  .\\venv\\Scripts\\python.exe diag_full.py [meli01,meli02,meli03]

Saída: resumo no terminal + respostas cruas em diag_full_output.json (não versionado).
"""

import asyncio
import json
import os
import sys
import time
import warnings
from datetime import datetime, timedelta

import httpx

import ml_api

warnings.filterwarnings("ignore")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_PATH = os.path.join(BASE_DIR, "diag_full_output.json")
B = ml_api.ML_BASE_URL

saida = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "contas": {}}
resumo = []  # (conta, endpoint, resultado)


# ─── HTTP ────────────────────────────────────────────────────────────────────

class Diag:
    def __init__(self, conta: str):
        self.conta = conta
        self.cli = httpx.Client(timeout=30, verify=False)
        self.renovou = False
        self.chamadas = []

    def get(self, rotulo: str, path: str, **params):
        """GET com 1 renovação de token em 401 e espera crescente em 429.
        Registra URL (sem token) e status."""
        espera, tentativas_429 = 2, 0
        while True:
            r = self.cli.get(B + path, params=params, headers=ml_api._headers(self.conta))
            if r.status_code == 401 and not self.renovou:
                self.renovou = True
                res = asyncio.run(ml_api.refresh_token(self.conta))
                print(f"   [{self.conta}] 401 → refresh_token: {'ok' if res.get('success') else 'FALHOU'}")
                continue
            if r.status_code == 429 and tentativas_429 < 5:
                tentativas_429 += 1
                time.sleep(espera)
                espera *= 2
                continue
            break
        if tentativas_429:
            print(f"   [{self.conta}] {rotulo}: {tentativas_429} espera(s) por 429 → HTTP {r.status_code}")
        try:
            body = r.json()
        except Exception:
            body = r.text[:500]
        nota = "falta permissão/scope no app" if r.status_code == 403 else ""
        self.chamadas.append({"rotulo": rotulo, "url": str(r.request.url), "status": r.status_code,
                              "nota": nota, "body": body})
        return r.status_code, body


def registrar(conta, endpoint, status):
    res = "OK" if status == 200 else str(status)
    if status == 403:
        res += " (falta permissão/scope)"
    resumo.append((conta, endpoint, res))


# ─── ETAPAS ──────────────────────────────────────────────────────────────────

def listar_ativos(d: Diag, seller_id: str) -> list:
    ids, scroll = [], None
    st, b = d.get("items_search", f"/users/{seller_id}/items/search", status="active", limit=100, offset=0)
    registrar(d.conta, "items/search", st)
    if st != 200:
        return []
    total = b["paging"]["total"]
    ids += b.get("results", [])
    if total > 1000:  # acima de 1000 exige search_type=scan
        ids = []
        while True:
            params = {"status": "active", "search_type": "scan", "limit": 100}
            if scroll:
                params["scroll_id"] = scroll
            st, b = d.get("items_search_scan", f"/users/{seller_id}/items/search", **params)
            if st != 200 or not b.get("results"):
                break
            ids += b["results"]
            scroll = b.get("scroll_id")
    else:
        offset = len(ids)
        while offset < total:
            st, b = d.get("items_search", f"/users/{seller_id}/items/search", status="active", limit=100, offset=offset)
            if st != 200 or not b.get("results"):
                break
            ids += b["results"]
            offset += len(b["results"])
    itens = []
    for i in range(0, len(ids), 20):
        st, lote = d.get("items_ids", "/items", ids=",".join(ids[i:i + 20]),
                         attributes="id,title,status,shipping,inventory_id,variations,seller_custom_field")
        if st == 200:
            itens += [e["body"] for e in lote if e.get("code") == 200]
    registrar(d.conta, "items?ids", 200 if itens or not ids else st)
    return itens


def inventory_ids(item: dict) -> list:
    """Lista (inventory_id, rótulo) do item e de cada variação."""
    out = []
    if item.get("inventory_id"):
        out.append((item["inventory_id"], "item"))
    for v in item.get("variations") or []:
        if v.get("inventory_id"):
            out.append((v["inventory_id"], f"variação {v.get('id')}"))
    return out


def diagnosticar(conta: str):
    cfg = ml_api.load_config()["contas"].get(conta)
    if not cfg:
        print(f"{conta}: não existe no config.json")
        return
    seller_id = str(cfg["seller_id"])
    d = Diag(conta)
    print(f"\n{'═' * 78}\n{conta.upper()}  (seller {seller_id})\n{'═' * 78}")

    # 1) anúncios ativos e Full
    itens = listar_ativos(d, seller_id)
    full = [i for i in itens if (i.get("shipping") or {}).get("logistic_type") == "fulfillment"]
    com_inv_item = [i for i in full if i.get("inventory_id")]
    com_var = [i for i in full if i.get("variations")]
    com_var_inv = [i for i in com_var if any(v.get("inventory_id") for v in i["variations"])]
    print(f"1) Ativos: {len(itens)} | em Full: {len(full)} | Full com inventory_id no item: {len(com_inv_item)}"
          f" | Full com variações: {len(com_var)} (com inventory_id por variação: {len(com_var_inv)})")

    # 2) escolher 3 MLBs em Full (1 com variações se houver)
    escolhidos = (com_var_inv[:1] + [i for i in full if i not in com_var_inv and inventory_ids(i)])[:3]
    estoque, status_vistos = {}, set()
    print(f"\n2) Estoque Full — MLBs: {[i['id'] for i in escolhidos]}")
    for it in escolhidos:
        for inv, rot in inventory_ids(it)[:3]:
            st, b = d.get("stock_fulfillment", f"/inventories/{inv}/stock/fulfillment")
            registrar(conta, "inventories/{id}/stock/fulfillment", st)
            estoque[inv] = {"mlb": it["id"], "titulo": it.get("title"), "rotulo": rot, "status": st, "body": b}
            print(f"\n   ── {it['id']} · {rot} · inventory {inv} · HTTP {st}")
            print("   " + json.dumps(b, ensure_ascii=False, indent=1).replace("\n", "\n   "))
            if st == 200 and isinstance(b, dict):
                det = b.get("not_available_detail") or []
                for x in det:
                    status_vistos.add(x.get("status"))
                print(f"   ▶ total={b.get('total')} | disponível={b.get('available_quantity', b.get('available_units'))}"
                      f" | indisponível={b.get('not_available_quantity', b.get('not_available_units'))}"
                      f" | detalhe={[(x.get('status'), x.get('quantity')) for x in det]}")
            # condições (detalhamento de danificados)
            st2, b2 = d.get("stock_fulfillment_conditions", f"/inventories/{inv}/stock/fulfillment",
                            include_attributes="conditions")
            registrar(conta, "stock/fulfillment?include_attributes=conditions", st2)

    # 3) operações dos últimos 30 dias
    ate = datetime.now().date()
    de = ate - timedelta(days=30)
    ops = {}
    print(f"\n3) Operações {de} → {ate}")
    for inv in list(estoque)[:3]:
        caminho = "/stock/fulfillment/operations/search"
        st, b = d.get("operations_search", caminho, seller_id=seller_id, inventory_id=inv,
                      date_from=de.isoformat(), date_to=ate.isoformat())
        if st == 404:
            caminho = "/marketplace/stock/fulfillment/operations/search"
            st, b = d.get("operations_search_marketplace", caminho, seller_id=seller_id, inventory_id=inv,
                          date_from=de.isoformat(), date_to=ate.isoformat())
        registrar(conta, caminho, st)
        res = b.get("results", []) if isinstance(b, dict) else []
        tipos = sorted({x.get("type") for x in res})
        ops[inv] = {"caminho": caminho, "status": st, "tipos": tipos,
                    "paging": b.get("paging") if isinstance(b, dict) else None, "exemplos": res[:2]}
        print(f"   inventory {inv}: HTTP {st} | {len(res)} operações na 1ª página"
              f" | paging={ops[inv]['paging']} | tipos={tipos}")
        ops[inv]["um_por_tipo"] = {t: next(x for x in res if x.get("type") == t) for t in tipos}
    vistos = set()
    for o in ops.values():
        for t, e in o.get("um_por_tipo", {}).items():
            if t in vistos:
                continue
            vistos.add(t)
            print(f"   exemplo {t}:", json.dumps(e, ensure_ascii=False)[:700])
    for o in ops.values():
        o.pop("um_por_tipo", None)

    # 4) inbound / envios ao Full a caminho
    print("\n4) Inbound / envios ao Full a caminho")
    candidatos = [
        ("fbm/orders (doc Global Selling)", "/marketplace/fbm/orders", {"seller_id": seller_id, "limit": 5}),
        ("fbm/orders sem /marketplace (palpite)", "/fbm/orders", {"seller_id": seller_id, "limit": 5}),
        ("operations INBOUND_RECEPTION 60d (doc)", "/stock/fulfillment/operations/search",
         {"seller_id": seller_id, "inventory_id": next(iter(estoque), ""), "type": "INBOUND_RECEPTION",
          "date_from": (ate - timedelta(days=60)).isoformat(), "date_to": ate.isoformat()}),
        ("inbounds/search (palpite)", "/stock/fulfillment/inbounds/search", {"seller_id": seller_id}),
    ]
    inbound = {}
    for rot, caminho, params in candidatos:
        st, b = d.get(f"inbound:{rot}", caminho, **params)
        registrar(conta, f"inbound: {rot}", st)
        inbound[rot] = {"status": st, "body": b}
        print(f"   {rot}: HTTP {st} → {json.dumps(b, ensure_ascii=False)[:300]}")

    saida["contas"][conta] = {
        "seller_id": seller_id,
        "contagem": {"ativos": len(itens), "full": len(full), "full_com_inventory_item": len(com_inv_item),
                     "full_com_variacoes": len(com_var), "full_com_inventory_por_variacao": len(com_var_inv)},
        "status_not_available_vistos": sorted(s for s in status_vistos if s),
        "estoque": estoque, "operacoes": ops, "inbound": inbound,
        "chamadas": [{k: v for k, v in c.items() if k != "body"} for c in d.chamadas],
    }


if __name__ == "__main__":
    contas = sys.argv[1].split(",") if len(sys.argv) > 1 else ["meli01", "meli02", "meli03"]
    for conta in contas:
        try:
            diagnosticar(conta)
        except Exception as e:
            print(f"{conta}: ERRO {e}")
            resumo.append((conta, "execução", f"ERRO {e}"))

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)

    # resumo conta × endpoint (último resultado de cada par)
    tabela = {}
    for conta, ep, res in resumo:
        tabela.setdefault(ep, {})[conta] = res
    print(f"\n{'═' * 78}\nRESUMO  conta × endpoint\n{'═' * 78}")
    print(f"{'endpoint':55} " + " ".join(f"{c:>14}" for c in contas))
    for ep, cols in tabela.items():
        print(f"{ep[:55]:55} " + " ".join(f"{cols.get(c, '—')[:14]:>14}" for c in contas))
    print(f"\nRespostas cruas: {OUT_PATH}")
