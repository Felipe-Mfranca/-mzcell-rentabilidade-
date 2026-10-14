"""
marketplaces/amazon/marketplace.py — Amazon dentro do contrato de marketplaces/base.py
MZCell Rentabilidade
"""

from marketplaces.base import Marketplace
from marketplaces.amazon import api


class AmazonMarketplace(Marketplace):
    nome = "amazon"

    def contas(self) -> list:
        return api.contas()

    def autenticado(self, conta: str) -> bool:
        return api.autenticado(conta)

    def listar_anuncios(self, conta: str) -> list:
        # Depende do role Product Listing, que ainda não foi mapeado pelo diagnóstico.
        raise NotImplementedError("Anúncios da Amazon ainda não implementados "
                                  "(role Product Listing a confirmar com o diagnóstico)")

    def listar_pedidos(self, conta: str, data_de: str, data_ate: str) -> list:
        # Pedidos trazem dados do comprador (PII) e dependem do role Inventory and Order Tracking.
        raise NotImplementedError("Pedidos da Amazon ainda não implementados")
