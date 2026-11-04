"""Captura diária de pedidos da Amazon (SP-API) para o financeiro.

Só leitura. Guarda em financeiro.db apenas o que mudou desde a última execução (LastUpdatedAfter),
com data e hora da captura; a "foto" de um dia sai do histórico (mesma ideia da captura do Mercado Livre).
Itens do pedido não são capturados (limite de chamadas da SP-API); o valor e o status vêm do pedido.

Uso:  python captura_pedidos_amazon.py [amazon01 amazon02] [--dias N]
      (padrão: as duas contas; --dias só vale na primeira execução de uma conta, padrão 45)
Agendamento: junto da captura do Meli (ver instalar_captura.ps1).
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from marketplaces.amazon import api  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "financeiro.db")
LOG_PATH = os.path.join(BASE, "captura_log.txt")
BRT = timezone(timedelta(hours=-3))
DIAS_PRIMEIRA = 45


def log(msg):
    linha = f"{datetime.now(BRT):%Y-%m-%d %H:%M:%S} {msg}"
    print(linha)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(linha + "\n")


def abrir_db():
    con = sqlite3.connect(DB_PATH)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS captura_estado (
        conta TEXT PRIMARY KEY,
        ultima_execucao TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS amazon_pedido_historico (
        conta TEXT NOT NULL,
        pedido_id TEXT NOT NULL,
        capturado_em TEXT NOT NULL,
        status TEXT,
        canal TEXT,
        compra_em TEXT,
        atualizado_em TEXT,
        total REAL,
        moeda TEXT,
        enviadas INTEGER,
        nao_enviadas INTEGER,
        PRIMARY KEY (conta, pedido_id, capturado_em)
    );
    """)
    return con


def pedir(conta, params):
    """GET /orders/v0/orders com espera longa em 429 (a cota de pedidos é de ~1 chamada por minuto após a rajada)."""
    for tentativa in range(6):
        status, corpo = api.get(conta, "/orders/v0/orders", **params)
        if status == 200:
            return corpo.get("payload") or {}
        if status == 429:
            time.sleep(65)
            continue
        raise api.falha(status, corpo)
    raise RuntimeError(f"{conta}: limite de tentativas em /orders/v0/orders")


def desde_da_ultima(con, conta, dias):
    row = con.execute("SELECT ultima_execucao FROM captura_estado WHERE conta=?", (conta,)).fetchone()
    if row:
        return datetime.fromisoformat(row[0]) - timedelta(days=1)  # sobreposição de 1 dia por segurança
    return datetime.now(BRT) - timedelta(days=dias)


def executar(conta, dias):
    if not api.autenticado(conta):
        raise RuntimeError("sem credenciais completas em amazon_config.json")
    con = abrir_db()
    agora_iso = datetime.now(BRT).isoformat(timespec="seconds")
    desde = desde_da_ultima(con, conta, dias)
    log(f"{conta}: capturando alterações desde {desde:%Y-%m-%d %H:%M}")
    params = {"MarketplaceIds": api.MARKETPLACE_BR, "MaxResultsPerPage": "100",
              "LastUpdatedAfter": desde.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    n = 0
    while True:
        payload = pedir(conta, params)
        for o in payload.get("Orders") or []:
            tot = o.get("OrderTotal") or {}
            try:
                valor = float(tot.get("Amount")) if tot.get("Amount") is not None else None
            except ValueError:
                valor = None
            con.execute(
                "INSERT OR REPLACE INTO amazon_pedido_historico VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (conta, o.get("AmazonOrderId"), agora_iso, o.get("OrderStatus"), o.get("FulfillmentChannel"),
                 o.get("PurchaseDate"), o.get("LastUpdateDate"), valor, tot.get("CurrencyCode"),
                 o.get("NumberOfItemsShipped"), o.get("NumberOfItemsUnshipped")))
            n += 1
        con.commit()
        prox = payload.get("NextToken")
        if not prox:
            break
        params = {"MarketplaceIds": api.MARKETPLACE_BR, "NextToken": prox}
    con.execute("INSERT OR REPLACE INTO captura_estado VALUES (?,?)", (conta, agora_iso))
    con.commit()
    con.close()
    log(f"{conta}: {n} pedidos gravados")


if __name__ == "__main__":
    args = sys.argv[1:]
    dias = DIAS_PRIMEIRA
    if "--dias" in args:
        i = args.index("--dias")
        dias = int(args[i + 1])
        del args[i:i + 2]
    for c in args or ["amazon01", "amazon02"]:
        try:
            executar(c, dias)
        except Exception as e:  # uma conta com erro não derruba as outras
            log(f"{c}: ERRO {type(e).__name__}: {api.mascarar(str(e))[:200]}")
