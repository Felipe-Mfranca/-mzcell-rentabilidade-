"""
modulos_liberados.py — Quais módulos em construção estão abertos nesta instalação
MZ App

O instalador leva o código de todos os módulos, mas eles saem FECHADOS (False). Para abrir um módulo,
muda-se o valor aqui para True, sobe-se o arquivo para o repositório e aumenta-se o version.txt:
o auto-update entrega o arquivo para todas as máquinas, sem novo instalador.

Só entram aqui módulos ainda em construção. Meli, Amazon, Shopee, Expedição e Bling seguem como estão.
"""

LIBERADOS = {
    "balanco": False,
    "compras": False,
    "radar": False,
    "montagem": False,    # fila de montagem dos envios Full (Meli, Amazon, Shopee) + painel de TV
    "financeiro": False,
    "magalu": False,      # integração Magalu (em implantação)
    "paineis": False,     # painéis de TV por área e gestão geral da diretoria
}

# Abertura POR MÁQUINA: o arquivo modulos_locais.json (fora do git e do instalador), por exemplo
# {"montagem": true, "financeiro": true}, abre o módulo só onde ele existir (servidor, máquina do Zicri).
import json
import os

_LOCAL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modulos_locais.json")


def _locais() -> dict:
    try:
        with open(_LOCAL, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def liberado(nome: str) -> bool:
    """Módulo desconhecido conta como fechado. Aberto se LIBERADOS ou o arquivo local da máquina disser True."""
    return bool(LIBERADOS.get(nome, False)) or _locais().get(nome) is True
