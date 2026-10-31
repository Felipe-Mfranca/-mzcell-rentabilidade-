"""
marketplaces/amazon/pivo_manual.py — PIVO manual dos SKUs da Amazon
MZ App

No Meli o PIVO é conferido pela API. Na Amazon não há leitura equivalente, então o analista marca cada letra
(P palavras-chave, I imagens, V vídeo, O otimização) como ok, falta ou não confirmado. Guarda em amazon_pivo.json (gitignored).
"""

import json
import os
import threading
from datetime import datetime

from marketplaces.amazon import api

CAMINHO = os.path.join(api.BASE_DIR, "amazon_pivo.json")
LETRAS = {"P": "Palavras-chave indexadas", "I": "Imagens", "V": "Vídeo", "O": "Otimização: promoção e ADS"}
_trava = threading.Lock()


def ler(conta: str) -> dict:
    """{SKU: {letra: True | False, "por": nome, "em": data}} — letra ausente = não confirmado."""
    try:
        with open(CAMINHO, encoding="utf-8") as f:
            return json.load(f).get(conta, {})
    except (FileNotFoundError, ValueError):
        return {}


def marcar(conta: str, sku: str, letra: str, valor, nome: str) -> dict:
    """valor True (ok), False (falta) ou None (volta a não confirmado)."""
    if letra not in LETRAS:
        raise ValueError("letra inválida")
    with _trava:
        try:
            with open(CAMINHO, encoding="utf-8") as f:
                db = json.load(f)
        except (FileNotFoundError, ValueError):
            db = {}
        item = db.setdefault(conta, {}).setdefault(sku, {})
        if valor is None:
            item.pop(letra, None)
        else:
            item[letra] = bool(valor)
        item["por"] = nome[:60]
        item["em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        with open(CAMINHO, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, ensure_ascii=False)
        return item
