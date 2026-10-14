"""Entrada em campanha de promoção do Mercado Livre — ESCREVE no ML.

Por padrão só SIMULA: mostra o que seria enviado e não altera nada. Só envia com confirmar=True
(CLI: --confirmar). Cada envio é registrado em promocoes_log.jsonl (sem tokens).

Tipos suportados (corpo do POST /seller-promotions/items/{mlb}?app_version=v2):
  LIGHTNING  promotion_id, promotion_type, deal_price (= preço da candidata), stock
             -> validado em produção (201); stock fica entre min e max da candidata e <= disponível
  SMART      promotion_id, promotion_type, offer_id (= ref_id da candidata)
  DEAL       promotion_id, promotion_type
Os dois últimos seguem a documentação do ML; se o ML recusar, o erro volta no resultado.
Outros tipos (PRICE_DISCOUNT, SELLER_CAMPAIGN...) não entram por aqui.

Não sai de campanha, não altera preço e não cria campanha. Só inscreve numa candidata existente.

Uso:
  python promocoes_ml.py meli03 MLB7580237540 LGH-MLB1000                 # simula
  python promocoes_ml.py meli03 MLB7580237540 LGH-MLB1000 --estoque 300   # simula, estoque 300
  python promocoes_ml.py meli03 MLB7580237540 LGH-MLB1000 --confirmar     # envia ao ML
"""
import argparse, asyncio, getpass, json, os, sys, warnings
from datetime import datetime

import httpx

warnings.filterwarnings("ignore")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import ml_api
from reposicao_full import ML

API = "https://api.mercadolibre.com"
LOG = os.path.join(BASE_DIR, "promocoes_log.jsonl")
TIPOS = ("LIGHTNING", "SMART", "DEAL")


class ErroPromocao(Exception):
    """Entrada recusada antes de chamar o ML (candidata inexistente, estoque inválido...)."""


def localizar_candidata(promos: list, promotion_id: str) -> dict:
    for p in promos or []:
        if p.get("id") == promotion_id:
            if p.get("status") != "candidate":
                raise ErroPromocao(f"{promotion_id} está '{p.get('status')}', não 'candidate' "
                                   "(já inscrita ou aplicada)")
            return p
    raise ErroPromocao(f"{promotion_id} não é candidata deste anúncio")


def montar_corpo(candidata: dict, disponivel: int, estoque: int = None) -> dict:
    tipo = candidata.get("type")
    if tipo not in TIPOS:
        raise ErroPromocao(f"tipo {tipo} não suportado (suportados: {', '.join(TIPOS)})")
    corpo = {"promotion_id": candidata["id"], "promotion_type": tipo}
    if tipo == "SMART":
        if not candidata.get("ref_id"):
            raise ErroPromocao("candidata SMART sem ref_id")
        corpo["offer_id"] = candidata["ref_id"]
    elif tipo == "LIGHTNING":
        if not candidata.get("price"):
            raise ErroPromocao("candidata LIGHTNING sem preço")
        lim = candidata.get("stock") or {}
        mn, mx = lim.get("min") or 1, lim.get("max") or disponivel
        if estoque is None:
            estoque = min(disponivel, mx)
        if not (mn <= estoque <= mx):
            raise ErroPromocao(f"estoque {estoque} fora do limite da campanha ({mn} a {mx})")
        if estoque > disponivel:
            raise ErroPromocao(f"estoque {estoque} maior que o disponível ({disponivel})")
        corpo["deal_price"] = candidata["price"]
        corpo["stock"] = estoque
    return corpo


def _registrar(registro: dict):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(registro, ensure_ascii=False) + "\n")


def _post(conta: str, mlb: str, corpo: dict):
    url = f"{API}/seller-promotions/items/{mlb}"
    for tentativa in (1, 2):
        r = httpx.post(url, params={"app_version": "v2"}, json=corpo, timeout=30, verify=False,
                       headers=ml_api._headers(conta))
        if r.status_code == 401 and tentativa == 1:
            asyncio.run(ml_api.refresh_token(conta))
            continue
        return r
    return r


def entrar(conta: str, mlb: str, promotion_id: str, estoque: int = None,
           confirmar: bool = False, usuario: str = None) -> dict:
    ml = ML(conta)
    st, promos = ml.get(f"/seller-promotions/items/{mlb}", app_version="v2")
    if st != 200 or not isinstance(promos, list):
        raise ErroPromocao(f"não consegui ler as promoções de {mlb} (HTTP {st})")
    st, item = ml.get(f"/items/{mlb}", attributes="id,title,available_quantity,status")
    if st != 200 or not isinstance(item, dict):
        raise ErroPromocao(f"não consegui ler o anúncio {mlb} (HTTP {st})")
    if item.get("status") != "active":
        raise ErroPromocao(f"anúncio {mlb} está '{item.get('status')}', não 'active'")

    candidata = localizar_candidata(promos, promotion_id)
    corpo = montar_corpo(candidata, item.get("available_quantity") or 0, estoque)
    resumo = {"conta": conta, "mlb": mlb, "titulo": item.get("title"),
              "campanha": candidata.get("name") or candidata["type"], "tipo": candidata["type"],
              "preco": candidata.get("price"), "corpo": corpo}
    if not confirmar:
        return {**resumo, "enviado": False}

    r = _post(conta, mlb, corpo)
    try:
        resposta = r.json()
    except ValueError:
        resposta = r.text[:300]
    status_depois = None
    if r.status_code in (200, 201):
        st, depois = ml.get(f"/seller-promotions/items/{mlb}", app_version="v2")
        for p in depois if isinstance(depois, list) else []:
            if p.get("id") == promotion_id:
                status_depois = p.get("status")
    resultado = {**resumo, "enviado": True, "http": r.status_code, "resposta": resposta,
                 "status_depois": status_depois, "ok": r.status_code in (200, 201)}
    _registrar({"quando": datetime.now().isoformat(timespec="seconds"),
                "usuario": usuario or getpass.getuser(), **{k: v for k, v in resultado.items()
                                                            if k != "titulo"}})
    return resultado


def main() -> int:
    ap = argparse.ArgumentParser(description="Inscreve um anúncio em campanha candidata do ML")
    ap.add_argument("conta"); ap.add_argument("mlb"); ap.add_argument("promotion_id")
    ap.add_argument("--estoque", type=int, help="LIGHTNING: unidades comprometidas")
    ap.add_argument("--confirmar", action="store_true", help="envia ao ML (sem isso, só simula)")
    a = ap.parse_args()
    try:
        r = entrar(a.conta, a.mlb, a.promotion_id, a.estoque, a.confirmar)
    except ErroPromocao as e:
        print(f"RECUSADO: {e}")
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if not r["enviado"]:
        print("\nSIMULAÇÃO — nada foi enviado. Repita com --confirmar para inscrever.")
        return 0
    print("\n" + ("INSCRITO." if r["ok"] else "O ML RECUSOU.") +
          f" HTTP {r['http']}, status depois: {r['status_depois']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
