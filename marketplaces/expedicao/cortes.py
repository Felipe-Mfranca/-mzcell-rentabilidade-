"""
marketplaces/expedicao/cortes.py — Horários de corte das plataformas que não vêm da API (Amazon DBA e Shopee)
MZ App

Os cortes do Mercado Livre vêm do prazo de cada envio (ml.prazos). Os da Amazon e da Shopee são regras da operação,
definidas pelo Zicri em 03/11/2026 e conferíveis nos painéis das plataformas (mudam com o tempo):

  DBA (Amazon)               11:00  impressão das etiquetas  ·  14:00  entrega no ponto de coleta
  Shopee Entrega Direta      12:00
  Shopee Xpress              13:00

Para ajustar sem mexer no código, crie cortes_plataformas.json (fora do git) na raiz, com a lista completa no mesmo formato:
  [{"plataforma": "Amazon", "canal": "DBA", "etapa": "Impressão das etiquetas", "corte": "11:00"}, ...]
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ARQUIVO = os.path.join(BASE_DIR, "cortes_plataformas.json")

PADRAO = [
    {"plataforma": "Amazon", "canal": "DBA", "etapa": "Impressão das etiquetas", "corte": "11:00"},
    {"plataforma": "Amazon", "canal": "DBA", "etapa": "Entrega no ponto de coleta", "corte": "14:00"},
    {"plataforma": "Shopee", "canal": "Entrega Direta", "etapa": "Corte do dia", "corte": "12:00"},
    {"plataforma": "Shopee", "canal": "Shopee Xpress", "etapa": "Corte do dia", "corte": "13:00"},
]


def _valido(item) -> bool:
    try:
        h, m = str(item["corte"]).split(":")
        return 0 <= int(h) <= 23 and 0 <= int(m) <= 59 and bool(item.get("canal"))
    except (KeyError, ValueError, AttributeError):
        return False


def carregar() -> list:
    """Lista de cortes. Arquivo local inválido ou ausente: usa o padrão (nunca derruba o painel)."""
    try:
        with open(ARQUIVO, encoding="utf-8") as f:
            lista = json.load(f)
        if isinstance(lista, list) and lista and all(_valido(i) for i in lista):
            return lista
    except (OSError, ValueError):
        pass
    return [dict(i) for i in PADRAO]
