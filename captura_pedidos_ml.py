"""Captura diária de pedidos e reclamações do Mercado Livre para o financeiro.

Só leitura na API do ML. Guarda em financeiro.db apenas o que mudou desde a última execução
(filtro order.date_last_updated), com data e hora da captura. A "foto" de um dia sai do histórico.

Uso:  python captura_pedidos_ml.py [meli01 meli02 meli03]   (padrão: meli01)
Agendamento: instalar_captura.ps1 (09:00, todo dia).
"""
import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ml_api  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "financeiro.db")
LOG_PATH = os.path.join(BASE, "captura_log.txt")
BRT = timezone(timedelta(hours=-3))
JANELA_RECLAMACOES_DIAS = 120


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
    CREATE TABLE IF NOT EXISTS pedido_historico (
        conta TEXT NOT NULL,
        pedido_id TEXT NOT NULL,
        capturado_em TEXT NOT NULL,
        pack_id TEXT,
        status TEXT,
        tags TEXT,
        total REAL,
        pago REAL,
        reembolsado REAL,
        shipping_id TEXT,
        last_updated TEXT,
        PRIMARY KEY (conta, pedido_id, capturado_em)
    );
    CREATE INDEX IF NOT EXISTS ix_pedido_pack ON pedido_historico (conta, pack_id);
    CREATE TABLE IF NOT EXISTS reclamacao_historico (
        conta TEXT NOT NULL,
        claim_id TEXT NOT NULL,
        capturado_em TEXT NOT NULL,
        recurso_id TEXT,
        recurso TEXT,
        tipo TEXT,
        etapa TEXT,
        status TEXT,
        motivo TEXT,
        resolucao TEXT,
        last_updated TEXT,
        PRIMARY KEY (conta, claim_id, capturado_em)
    );
    """)
    return con


def get(cli, conta, path, params=None):
    """GET com renovação de token (1x) e espera em 429. Erro de rede/500 aborta a conta."""
    renovou = False
    for tentativa in range(8):
        r = cli.get(f"{ml_api.ML_BASE_URL}{path}", params=params,
                    headers={"Authorization": f"Bearer {ml_api.get_token(conta)}"})
        if r.status_code == 401 and not renovou:
            asyncio.run(ml_api.refresh_token(conta))
            renovou = True
            continue
        if r.status_code == 429:
            time.sleep(3 + tentativa * 3)
            continue
        if r.status_code != 200:
            raise RuntimeError(f"{conta} {path} HTTP {r.status_code}: {r.text[:120]}")
        return r.json()
    raise RuntimeError(f"{conta} {path}: limite de tentativas")


def desde_da_ultima(con, conta):
    row = con.execute("SELECT ultima_execucao FROM captura_estado WHERE conta=?", (conta,)).fetchone()
    if row:
        return datetime.fromisoformat(row[0]) - timedelta(days=1)  # sobreposição de 1 dia por segurança
    return datetime.now(BRT) - timedelta(days=60)


def capturar_pedidos(cli, con, conta, desde, agora_iso):
    sid = ml_api.get_seller_id(conta)
    n = 0
    dia = desde.astimezone(BRT).replace(hour=0, minute=0, second=0, microsecond=0)
    fim = datetime.now(BRT)
    while dia <= fim:
        fim_dia = dia + timedelta(days=1) - timedelta(seconds=1)
        off = 0
        while True:
            d = get(cli, conta, "/orders/search", {
                "seller": sid,
                "order.date_last_updated.from": dia.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
                "order.date_last_updated.to": fim_dia.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
                "limit": 50, "offset": off})
            res = d.get("results") or []
            for o in res:
                pay = (o.get("payments") or [{}])[0]
                con.execute(
                    "INSERT OR REPLACE INTO pedido_historico VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (conta, str(o["id"]), agora_iso, str(o.get("pack_id") or ""), o.get("status"),
                     json.dumps(o.get("tags") or []), o.get("total_amount"), pay.get("transaction_amount"),
                     pay.get("transaction_amount_refunded"), str((o.get("shipping") or {}).get("id") or ""),
                     o.get("last_updated")))
                n += 1
            off += len(res)
            if not res or off >= d["paging"]["total"]:
                break
        dia += timedelta(days=1)
    return n


def capturar_reclamacoes(cli, con, conta, desde, agora_iso):
    n = 0
    limite = datetime.now(BRT) - timedelta(days=JANELA_RECLAMACOES_DIAS)
    for status in ("opened", "closed"):
        off = 0
        while True:
            d = get(cli, conta, "/post-purchase/v1/claims/search", {
                "status": status, "limit": 50, "offset": off, "sort": "last_updated:desc"})
            lote = d.get("data") or []
            parar = False
            for c in lote:
                upd = datetime.fromisoformat(c["last_updated"].replace("Z", "+00:00"))
                if upd < desde:
                    parar = True
                    break
                if upd < limite:
                    continue
                res = c.get("resolution") or {}
                con.execute(
                    "INSERT OR REPLACE INTO reclamacao_historico VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (conta, str(c["id"]), agora_iso, str(c.get("resource_id") or ""), c.get("resource"),
                     c.get("type"), c.get("stage"), c.get("status"), c.get("reason_id"),
                     res.get("reason"), c.get("last_updated")))
                n += 1
            off += len(lote)
            if parar or not lote or off >= d.get("paging", {}).get("total", 0):
                break
    return n


def executar(conta):
    con = abrir_db()
    agora = datetime.now(BRT)
    agora_iso = agora.isoformat(timespec="seconds")
    desde = desde_da_ultima(con, conta)
    log(f"{conta}: capturando alterações desde {desde:%Y-%m-%d %H:%M}")
    with httpx.Client(timeout=30, verify=False) as cli:
        np = capturar_pedidos(cli, con, conta, desde, agora_iso)
        nc = capturar_reclamacoes(cli, con, conta, desde, agora_iso)
    con.execute("INSERT OR REPLACE INTO captura_estado VALUES (?,?)", (conta, agora_iso))
    con.commit()
    con.close()
    log(f"{conta}: {np} pedidos e {nc} reclamações gravados")


if __name__ == "__main__":
    contas = sys.argv[1:] or ["meli01"]
    for c in contas:
        try:
            executar(c)
        except Exception as e:  # uma conta com erro não derruba as outras
            log(f"{c}: ERRO {type(e).__name__}: {str(e)[:200]}")
