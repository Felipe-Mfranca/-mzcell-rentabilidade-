"""Clona uma campanha do vendedor (SELLER_CAMPAIGN) para o próximo período — ESCREVE no ML.

Por padrão só SIMULA: lê a campanha de origem, confere cada produto e mostra o que seria enviado.
Só cria e inscreve com --confirmar. Cada envio é registrado em promocoes_log.jsonl (sem tokens).

O que faz:
  1. cria a campanha nova (POST /seller-promotions/promotions), ou reaproveita --destino se já existir
  2. inscreve cada produto ATIVO da origem pelo mesmo preço (POST /seller-promotions/items/{mlb})
Produtos 'candidate' na origem (não inscritos) não são clonados. Não sai de campanha, não muda preço
de anúncio fora da campanha, não mexe em campanhas do ML.

Uso:
  python clonar_campanha.py meli02 C-MLB5481571 --inicio 2026-10-28 --fim 2026-11-26            # simula
  python clonar_campanha.py meli02 C-MLB5481571 --inicio 2026-10-28 --fim 2026-11-26 --confirmar
  python clonar_campanha.py meli01 C-MLB5480802 --destino C-MLB6053733 --inicio 2026-10-20 --fim 2026-11-19
"""
import argparse, asyncio, getpass, json, os, sys, time, warnings
from datetime import date, datetime

import httpx

warnings.filterwarnings("ignore")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ml_api
from reposicao_full import ML

API = "https://api.mercadolibre.com"
LOG = os.path.join(BASE_DIR, "promocoes_log.jsonl")
MAX_DIAS = 30
MESES = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


class ErroClone(Exception):
    """Recusado antes de chamar o ML (datas, origem inválida...)."""


def nome_padrao(inicio: date, fim: date) -> str:
    return f"{inicio.day} {MESES[inicio.month - 1]} a {fim.day} {MESES[fim.month - 1]}"


def validar_datas(inicio: date, fim: date, fim_origem: date) -> int:
    dias = (fim - inicio).days + 1
    if fim < inicio:
        raise ErroClone("a data final é anterior à inicial")
    if dias > MAX_DIAS:
        raise ErroClone(f"período de {dias} dias passa de {MAX_DIAS}")
    if inicio <= fim_origem:
        raise ErroClone(f"o início ({inicio}) precisa ser depois do fim da origem ({fim_origem})")
    return dias


def itens_da_campanha(ml: ML, promocao: str) -> list:
    saida, depois = [], None
    while True:
        kw = dict(promotion_type="SELLER_CAMPAIGN", app_version="v2", limit=50)
        if depois:
            kw["search_after"] = depois
        st, b = ml.get(f"/seller-promotions/promotions/{promocao}/items", **kw)
        if st != 200 or not isinstance(b, dict):
            raise ErroClone(f"não consegui ler os itens de {promocao} (HTTP {st})")
        res = b.get("results") or []
        saida += res
        depois = (b.get("paging") or {}).get("searchAfter")
        if not res or not depois:
            return saida


def tambem_em(promos: list, origem: str) -> str:
    """Outras promoções ativas/pendentes do produto. É só informação: o ML aceita a campanha do vendedor
    junto com Smart/Deal (hoje esses mesmos produtos já estão nas duas); se recusar, o erro vem no envio."""
    outras = [f"{p.get('type')}" for p in promos or []
              if p.get("id") != origem and p.get("status") in ("started", "pending")
              and p.get("type") != "SELLER_CAMPAIGN"]
    return ", ".join(sorted(set(outras)))


def planejar(ml: ML, origem: str, inicio: date, fim: date) -> dict:
    st, det = ml.get(f"/seller-promotions/promotions/{origem}", promotion_type="SELLER_CAMPAIGN", app_version="v2")
    if st != 200 or det.get("type") != "SELLER_CAMPAIGN":
        raise ErroClone(f"{origem} não é uma campanha do vendedor legível (HTTP {st})")
    fim_origem = date.fromisoformat(det["finish_date"][:10]) if det.get("finish_date") else date.min
    # finish_date vem em UTC (…T02:59:59Z = 23:59 do dia anterior em Brasília)
    if det.get("finish_date", "").endswith("Z") and "T02:59" in det["finish_date"]:
        fim_origem = date.fromordinal(fim_origem.toordinal() - 1)
    dias = validar_datas(inicio, fim, fim_origem)
    todos = itens_da_campanha(ml, origem)
    ativos = [i for i in todos if i.get("status") == "started"]
    linhas = []
    for i in ativos:
        mlb = i["id"]
        st, it = ml.get(f"/items/{mlb}", attributes="id,title,status,available_quantity")
        st2, promos = ml.get(f"/seller-promotions/items/{mlb}", app_version="v2")
        motivo, obs = None, ""
        if st != 200 or it.get("status") != "active":
            motivo = f"anúncio {it.get('status') if st == 200 else 'ilegível'}"
        elif not (it.get("available_quantity") or 0):
            motivo = "sem estoque"
        elif st2 != 200 or not isinstance(promos, list):
            motivo = "não consegui ler as promoções do anúncio"
        else:
            obs = tambem_em(promos, origem)
        linhas.append({"mlb": mlb, "titulo": (it or {}).get("title"), "preco": i.get("price"),
                       "preco_cheio": i.get("original_price"), "ok": motivo is None, "motivo": motivo,
                       "obs": obs if motivo is None and st2 == 200 else ""})
    return {"origem": det, "dias": dias, "total_origem": len(todos), "ativos": len(ativos),
            "nao_inscritos": len(todos) - len(ativos), "linhas": linhas}


def _post(conta: str, caminho: str, corpo: dict):
    for tentativa in (1, 2):
        r = httpx.post(f"{API}{caminho}", params={"app_version": "v2"}, json=corpo, timeout=30,
                       verify=False, headers=ml_api._headers(conta))
        if r.status_code == 401 and tentativa == 1:
            asyncio.run(ml_api.refresh_token(conta))
            continue
        return r
    return r


def _registrar(registro: dict):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _resp(r):
    try:
        return r.json()
    except ValueError:
        return r.text[:300]


def executar(conta: str, plano: dict, origem: str, inicio: date, fim: date, nome: str,
             destino: str = None, usuario: str = None) -> dict:
    quando = lambda: datetime.now().isoformat(timespec="seconds")
    quem = usuario or getpass.getuser()
    if not destino:
        corpo = {"promotion_type": "SELLER_CAMPAIGN", "sub_type": "FLEXIBLE_PERCENTAGE", "name": nome,
                 "start_date": f"{inicio.isoformat()}T00:00:00", "finish_date": f"{fim.isoformat()}T23:59:59"}
        r = _post(conta, "/seller-promotions/promotions", corpo)
        resp = _resp(r)
        _registrar({"quando": quando(), "usuario": quem, "acao": "CRIAR_CAMPANHA", "conta": conta,
                    "origem": origem, "corpo": corpo, "http": r.status_code, "resposta": resp})
        if r.status_code not in (200, 201) or not isinstance(resp, dict) or not resp.get("id"):
            return {"criada": False, "http": r.status_code, "resposta": resp, "resultados": []}
        destino = resp["id"]
    resultados, falhas_seguidas = [], 0
    ml = ML(conta)
    for l in plano["linhas"]:
        if not l["ok"]:
            resultados.append({**l, "enviado": False})
            continue
        corpo = {"promotion_id": destino, "promotion_type": "SELLER_CAMPAIGN", "deal_price": l["preco"]}
        r = _post(conta, f"/seller-promotions/items/{l['mlb']}", corpo)
        ok = r.status_code in (200, 201)
        resp = _resp(r)
        _registrar({"quando": quando(), "usuario": quem, "acao": "INSCREVER_ITEM", "conta": conta,
                    "mlb": l["mlb"], "corpo": corpo, "http": r.status_code, "resposta": resp, "ok": ok})
        resultados.append({**l, "enviado": True, "http": r.status_code, "ok": ok,
                           "resposta": None if ok else resp})
        falhas_seguidas = 0 if ok else falhas_seguidas + 1
        if falhas_seguidas >= 3:
            resultados.append({"abortado": "3 falhas seguidas; restante não enviado"})
            break
        time.sleep(0.3)
    inscritos = None
    try:
        itens = itens_da_campanha(ml, destino)
        inscritos = sum(1 for i in itens if i.get("status") in ("pending", "started"))
    except ErroClone:
        pass
    return {"criada": True, "destino": destino, "resultados": resultados, "inscritos_na_destino": inscritos}


def main() -> int:
    ap = argparse.ArgumentParser(description="Clona uma campanha do vendedor para o próximo período")
    ap.add_argument("conta"); ap.add_argument("origem")
    ap.add_argument("--inicio", required=True, help="AAAA-MM-DD"); ap.add_argument("--fim", required=True, help="AAAA-MM-DD")
    ap.add_argument("--nome"); ap.add_argument("--destino", help="campanha já criada (não cria outra)")
    ap.add_argument("--confirmar", action="store_true", help="cria e inscreve no ML (sem isso, só simula)")
    a = ap.parse_args()
    try:
        inicio, fim = date.fromisoformat(a.inicio), date.fromisoformat(a.fim)
        nome = a.nome or nome_padrao(inicio, fim)
        plano = planejar(ML(a.conta), a.origem, inicio, fim)
    except (ErroClone, ValueError) as e:
        print(f"RECUSADO: {e}")
        return 1
    ok = [l for l in plano["linhas"] if l["ok"]]
    print(f"Origem {a.origem} \"{plano['origem'].get('name')}\" -> nova \"{nome}\" de {inicio:%d/%m/%Y} a {fim:%d/%m/%Y} ({plano['dias']} dias)")
    print(f"Itens na origem: {plano['total_origem']} | ativos (a clonar): {plano['ativos']} | não inscritos (ignorados): {plano['nao_inscritos']}")
    print(f"Podem entrar: {len(ok)} | bloqueados: {len(plano['linhas']) - len(ok)}\n")
    for l in plano["linhas"]:
        print(f"  {'OK ' if l['ok'] else 'NÃO'} {l['mlb']}  R$ {l['preco']:>8}  (cheio {l['preco_cheio']})  {(l['titulo'] or '')[:40]}"
              + ("" if l["ok"] else f"  <- {l['motivo']}") + (f"  [também em {l['obs']}]" if l.get("obs") else ""))
    if not a.confirmar:
        print("\nSIMULAÇÃO — nada foi enviado. Repita com --confirmar para criar a campanha e inscrever.")
        return 0
    r = executar(a.conta, plano, a.origem, inicio, fim, nome, a.destino)
    if not r["criada"]:
        print(f"\nO ML RECUSOU criar a campanha: HTTP {r['http']} {json.dumps(r['resposta'], ensure_ascii=False)[:400]}")
        return 1
    env = [x for x in r["resultados"] if x.get("enviado")]
    print(f"\nCampanha {r['destino']}: {sum(1 for x in env if x['ok'])} inscritos, {sum(1 for x in env if not x['ok'])} recusados; "
          f"inscritos na campanha agora: {r['inscritos_na_destino']}")
    for x in env:
        if not x["ok"]:
            print(f"  RECUSADO {x['mlb']}: HTTP {x['http']} {json.dumps(x['resposta'], ensure_ascii=False)[:200]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
