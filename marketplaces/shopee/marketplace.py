"""
marketplaces/shopee/marketplace.py — Shopee dentro do contrato de marketplaces/base.py
MZ App

Aqui "conta" é a loja (id de shopee_lojas.json). Anúncios e pedidos entram nas fases 2 e 3.
"""

from marketplaces.base import Marketplace
from marketplaces.shopee import api, lojas


class ShopeeMarketplace(Marketplace):
    nome = "shopee"

    def contas(self) -> list:
        return list(lojas.lojas())

    def autenticado(self, conta: str) -> bool:
        return api.estado_autorizacao(lojas.shop_id_da(conta))["autorizada"]

    def listar_anuncios(self, conta: str) -> list:
        raise NotImplementedError("Anúncios da Shopee ainda não implementados (fase 4)")

    def listar_pedidos(self, conta: str, data_de: str, data_ate: str) -> list:
        raise NotImplementedError("Pedidos da Shopee ainda não implementados (fase 2)")
