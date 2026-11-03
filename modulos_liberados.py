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
}


def liberado(nome: str) -> bool:
    """Módulo desconhecido conta como fechado."""
    return bool(LIBERADOS.get(nome, False))
