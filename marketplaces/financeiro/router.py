"""
marketplaces/financeiro/router.py — Base do módulo Financeiro (em implantação, somente leitura)
MZ App

GET /financeiro          página do módulo
GET /financeiro/estado   o que já existe: capturas diárias no financeiro.db (contagens) e as fases do módulo

O desenvolvimento do módulo (linhas 01–10 do DRE, reversões e contestações, extrato e Sispag) acontece neste
diretório e chega às máquinas pelo auto-update. Fechado por padrão (modulos_liberados.py → "financeiro").
"""

import os
import sqlite3

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/financeiro", tags=["financeiro"])
PASTA = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(os.path.dirname(PASTA))
DB = os.path.join(BASE, "financeiro.db")
TABELAS = ("pedido_historico", "reclamacao_historico", "amazon_pedido_historico")

FASES = [
    {"fase": "Capturas diárias (pedidos e reclamações ML, pedidos Amazon)", "estado": "pronto"},
    {"fase": "Classificação das linhas 01–10 por conta", "estado": "em_desenvolvimento"},
    {"fase": "Reversões e contestações de frete", "estado": "planejado"},
    {"fase": "Extrato bancário e Sispag", "estado": "planejado"},
]


@router.get("", response_class=HTMLResponse)
def pagina():
    with open(os.path.join(PASTA, "pagina.html"), encoding="utf-8") as f:
        return f.read()


@router.get("/estado")
def estado():
    capturas = {}
    if os.path.exists(DB):
        try:
            con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
            for t in TABELAS:
                try:
                    capturas[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except sqlite3.Error:
                    capturas[t] = None
            con.close()
        except sqlite3.Error:
            pass
    return {"modulo": "financeiro", "situacao": "em_implantacao", "banco_existe": os.path.exists(DB),
            "capturas": capturas, "fases": FASES}
