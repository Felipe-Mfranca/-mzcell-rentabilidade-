"""
marketplaces/magalu/router.py — Base do módulo Magalu (em implantação)
MZ App

GET /magalu/estado   situação da integração. Ainda não há credencial nem chamada à API do Magalu.

A integração (autorização, pedidos, estoque, margem) é construída neste diretório e chega às máquinas pelo
auto-update. Credenciais futuras: magalu_config.json (já no padrão dos outros marketplaces; nunca commitar).
Fechado por padrão (modulos_liberados.py → "magalu"). Empresa: Techseller (ver mapeamento de contas).
"""

import os

from fastapi import APIRouter

router = APIRouter(prefix="/magalu", tags=["magalu"])
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ETAPAS = [
    {"etapa": "Credenciais e autorização (OAuth)", "estado": "planejado"},
    {"etapa": "Pedidos e prazos de postagem", "estado": "planejado"},
    {"etapa": "Estoque e anúncios", "estado": "planejado"},
    {"etapa": "Margem e DRE da conta", "estado": "planejado"},
]


@router.get("/estado")
def estado():
    return {"modulo": "magalu", "situacao": "em_implantacao", "empresa": "Techseller",
            "credencial_configurada": os.path.exists(os.path.join(BASE, "magalu_config.json")), "etapas": ETAPAS}
