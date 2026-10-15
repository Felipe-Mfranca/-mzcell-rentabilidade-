"""Verificação agendada de promoções/rebot — SOMENTE LEITURA (nunca escreve no ML).

Para cada conta (meli01/02/03) compara a promoção atual de cada anúncio com as candidatas:
  líquido ML/un = preço − (tarifa% × preço − rebot)        rebot = meli% × preço cheio
  ganho/un     = líquido da candidata − líquido atual
Só recomenda o que vale a pena: ganho > 0, ou empate de preço e líquido em que a atual é nossa
e a candidata é do ML (preferência pela campanha do ML). Candidatas já inscritas (pending) e
anúncios já avisados antes não entram. Sem novidade, apenas informa.

Uso:
  python rebot_agenda.py                  # roda, avisa (Slack + e-mail) e guarda o que já foi avisado
  python rebot_agenda.py --dry            # só imprime; não envia nem grava estado
  python rebot_agenda.py --contas meli02  # restringe as contas

Config (todos no .gitignore): slack_config.json (bot_token + destinatarios, ou webhook_url),
email_config.json (SMTP do app) e rebot_agenda_config.json ({"email_destinatarios": [...]}).
"""
import argparse, json, os, smtplib, sys, time, traceback, warnings
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

warnings.filterwarnings("ignore")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR); os.chdir(BASE_DIR)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import ml_api
from reposicao_full import ML
import buybox_monitor
import rebot_visual

CONTAS_PADRAO = ["meli01", "meli02", "meli03"]
# promoções criadas por nós (vendedor); o resto é campanha do ML
NOSSAS = {"SELLER_CAMPAIGN", "PRICE_DISCOUNT", "PROPRIA", "SEM_PROMOCAO"}
ESTADO = os.path.join(BASE_DIR, "rebot_agenda_estado.json")
CONFIG = os.path.join(BASE_DIR, "rebot_agenda_config.json")
LOG = os.path.join(BASE_DIR, "rebot_agenda.log")


def log(msg: str):
    linha = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(linha, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def _json(path: str, padrao):
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return padrao


def vendas_30d(ml: ML, sid: str) -> dict:
    """Unidades pagas por MLB nos últimos 30 dias (uma varredura de pedidos por conta)."""
    de = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000-03:00")
    un, off = {}, 0
    while off < 10000:
        st, b = ml.get("/orders/search", seller=sid, sort="date_desc", limit=50, offset=off,
                       **{"order.date_created.from": de})
        res = (b or {}).get("results") or []
        for o in res:
            if o.get("status") != "paid":
                continue
            for i in o.get("order_items", []):
                un[i["item"]["id"]] = un.get(i["item"]["id"], 0) + i.get("quantity", 0)
        off += len(res)
        if not res or off >= (b.get("paging") or {}).get("total", 0):
            break
    return un


def analisar_conta(conta: str) -> list:
    ml = ML(conta)
    sid = str(ml_api.load_config()["contas"][conta]["seller_id"])
    ids, off = [], 0
    while True:
        st, b = ml.get(f"/users/{sid}/items/search", status="active", limit=100, offset=off)
        if st != 200:
            raise RuntimeError(f"lista de anúncios HTTP {st}")
        res = b.get("results") or []
        ids += res; off += len(res)
        if not res or off >= b["paging"]["total"]:
            break
    itens = {}
    for i in range(0, len(ids), 20):
        st, lote = ml.get("/items", ids=",".join(ids[i:i + 20]),
                          attributes="id,title,price,category_id,listing_type_id")
        for e in lote or []:
            if e.get("code") == 200:
                itens[e["body"]["id"]] = e["body"]
    if ids and not itens:
        raise RuntimeError("nenhum detalhe de anúncio retornado")

    vendas = vendas_30d(ml, sid)
    tarifas = {}

    def tarifa(preco, it):
        k = (round(preco, 2), it["listing_type_id"], it["category_id"])
        if k not in tarifas:
            st, lp = ml.get("/sites/MLB/listing_prices", price=preco,
                            listing_type_id=k[1], category_id=k[2])
            lp = lp[0] if isinstance(lp, list) else (lp or {})
            tarifas[k] = ((lp.get("sale_fee_details") or {}).get("percentage_fee") or 0) / 100
        return tarifas[k]

    achados = []
    for mlb, it in itens.items():
        st, sp = ml.get(f"/items/{mlb}/sale_price", context="channel_marketplace")
        st2, pr = ml.get(f"/seller-promotions/items/{mlb}", app_version="v2")
        if not isinstance(sp, dict) or not isinstance(pr, list):
            continue
        atual_preco = sp.get("amount") or it["price"]
        cheio = sp.get("regular_amount") or it["price"]
        meta = sp.get("metadata") or {}
        pid, cid = meta.get("promotion_id"), meta.get("campaign_id")
        ativa = next((p for p in pr if p.get("status") == "started"
                      and (p.get("ref_id") == pid or p.get("id") == cid)), None)
        a_tipo = (ativa or {}).get("type") or ("SEM_PROMOCAO" if not pid else "PROPRIA")
        a_rebot = ((ativa or {}).get("meli_percentage") or 0) / 100 * ((ativa or {}).get("original_price") or cheio)
        a_liq = atual_preco - (tarifa(atual_preco, it) * atual_preco - a_rebot)
        a_nome = (ativa or {}).get("name") or ("sem promoção" if not pid else "promoção própria")

        melhor = None
        for p in pr:
            if p.get("status") != "candidate" or not p.get("price"):
                continue  # pending/started: já inscrita ou aplicada
            preco = p["price"]
            rebot = (p.get("meli_percentage") or 0) / 100 * (p.get("original_price") or cheio)
            liq = preco - (tarifa(preco, it) * preco - rebot)
            ganho, dp = round(liq - a_liq, 2), round(preco - atual_preco, 2)
            empate = abs(ganho) < 0.01 and abs(dp) < 0.01
            if empate:
                if not (a_tipo in NOSSAS and p.get("type") not in NOSSAS):
                    continue
            elif ganho <= 0:
                continue
            if dp > 0 and rebot - a_rebot <= 0.005:
                continue  # ganho só do preço mais alto: rebot é desconto pago pelo ML, isso não é rebot
            c = {"conta": conta, "mlb": mlb, "titulo": it["title"], "un_30d": vendas.get(mlb, 0),
                 "atual_nome": a_nome, "atual_preco": atual_preco, "atual_rebot": round(a_rebot, 2),
                 "tipo": p.get("type"), "promo_id": p.get("id") or p.get("type"),
                 "nome": p.get("name") or p.get("type"), "preco": preco, "rebot": round(rebot, 2),
                 "ganho_un": ganho, "delta_preco": dp, "empate": empate,
                 "estoque": p.get("stock"), "fim": (p.get("finish_date") or "")[:10]}
            c["impacto_mes"] = round(ganho * c["un_30d"], 2)
            if melhor is None or (ganho, c["un_30d"]) > (melhor["ganho_un"], melhor["un_30d"]):
                melhor = c
        if melhor:
            melhor["chave"] = f"{conta}|{mlb}|{melhor['promo_id']}|{melhor['preco']}"
            achados.append(melhor)
    log(f"{conta}: {len(itens)} anúncios, {len(achados)} oportunidades, {sum(vendas.values())} un/30d")
    return achados


def linha(o: dict) -> str:
    extras = []
    if o["empate"]:
        extras.append("empate: preferir campanha do ML")
    elif o["delta_preco"] > 0:
        extras.append(f"⚠ preço sobe R$ {o['delta_preco']:.2f} — checar buybox")
    if o["tipo"] == "LIGHTNING" and o["estoque"]:
        extras.append(f"exige estoque {o['estoque'].get('min')}–{o['estoque'].get('max')} un")
    if o["fim"]:
        extras.append(f"até {o['fim']}")
    ganho = "empate" if o["empate"] else f"{o['ganho_un']:+.2f}/un"
    imp = f" ≈ R$ {o['impacto_mes']:+.0f}/mês" if o["un_30d"] and not o["empate"] else ""
    return (f"• {o['mlb']} {o['titulo'][:40]} — {o['nome']}: R$ {o['atual_preco']:.2f} "
            f"(rebot {o['atual_rebot']:.2f}) → R$ {o['preco']:.2f} (rebot {o['rebot']:.2f}) | "
            f"{ganho}{imp} | {o['un_30d']} un/30d" + (f" | {'; '.join(extras)}" if extras else ""))


def montar(novas: list, erros: dict, abertas: int, quando: str) -> tuple:
    """(assunto, texto, html). O texto vai ao Slack; o e-mail leva texto + HTML."""
    if not novas and not erros:
        resto = f" ({abertas} já avisada(s) continuam abertas)" if abertas else ""
        t = f"Rebot {quando}: sem oportunidades novas nas contas verificadas{resto}."
        return "MZCell - Rebot: sem novidades", t, None
    assunto = f"MZCell - Rebot: {len(novas)} oportunidade(s)" + (" (com erro)" if erros else "")
    return (assunto,
            rebot_visual.texto(novas, erros, abertas, quando, CONTAS_PADRAO),
            rebot_visual.html(novas, erros, abertas, quando, CONTAS_PADRAO))


def enviar_email(assunto: str, texto: str, html: str = None) -> bool:
    try:
        cfg = json.load(open(os.path.join(BASE_DIR, "email_config.json"), encoding="utf-8"))
        dest = _json(CONFIG, {}).get("email_destinatarios") or [cfg["email_remetente"]]
        plano = texto.replace("*", "").replace("`", "").replace("_", "")
        if html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(plano, "plain", "utf-8"))
            msg.attach(MIMEText(html, "html", "utf-8"))
        else:
            msg = MIMEText(plano, "plain", "utf-8")
        msg["From"], msg["To"], msg["Subject"] = cfg["email_remetente"], ", ".join(dest), assunto
        with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"], timeout=30) as s:
            s.starttls(); s.login(cfg["email_remetente"], cfg["senha_app"]); s.send_message(msg)
        log(f"e-mail enviado para {', '.join(dest)}")
        return True
    except Exception as e:
        log(f"e-mail FALHOU: {type(e).__name__}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="só imprime; não envia nem grava estado")
    ap.add_argument("--semear", action="store_true",
                    help="marca as oportunidades atuais como já avisadas, sem enviar nada")
    ap.add_argument("--contas", nargs="+", default=CONTAS_PADRAO)
    a = ap.parse_args()

    estado = _json(ESTADO, {"avisadas": []})
    avisadas = set(estado.get("avisadas", []))
    todas, erros = [], {}
    for conta in a.contas:
        try:
            todas += analisar_conta(conta)
        except Exception as e:
            erros[conta] = f"{type(e).__name__}: {str(e)[:80]}"
            log(f"{conta}: ERRO {traceback.format_exc(limit=2)}")
    novas = [o for o in todas if o["chave"] not in avisadas]
    abertas = len(todas) - len(novas)
    quando = datetime.now().strftime("%d/%m %H:%M")
    assunto, texto, html = montar(novas, erros, abertas, quando)
    print("\n" + texto)
    if a.dry:
        return 1 if erros else 0
    if a.semear:
        if erros:
            log("semear cancelado: houve erro em alguma conta")
            return 1
        estado["avisadas"] = sorted(o["chave"] for o in todas)
        estado["ultima_execucao"] = datetime.now().isoformat()
        json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log(f"estado semeado com {len(todas)} oportunidade(s); nada enviado")
        return 0

    ok_slack = buybox_monitor.enviar_slack(texto)
    ok_mail = enviar_email(assunto, texto, html)
    if ok_slack or ok_mail:
        # só marca como avisada se algum canal entregou; senão tenta de novo na próxima rodada
        estado["avisadas"] = sorted(avisadas & {o["chave"] for o in todas} | {o["chave"] for o in novas})
        estado["ultima_execucao"] = datetime.now().isoformat()
        json.dump(estado, open(ESTADO, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    else:
        log("nenhum canal entregou — estado não gravado")
    return 1 if (erros or not (ok_slack or ok_mail)) else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # agendado com pythonw não há console: sem isto, um erro fatal não deixaria rastro
        log("ERRO FATAL:\n" + traceback.format_exc())
        sys.exit(2)
