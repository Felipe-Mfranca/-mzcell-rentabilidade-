"""
marketplaces/base.py — Interface comum dos módulos de marketplace (rascunho v0)
MZ App

Cada marketplace (ML, Amazon, Shopee, Bling...) vive em marketplaces/<nome>/ e expõe:
  - uma classe que herda de Marketplace
  - um router FastAPI em router.py, montado no main.py com app.include_router()

Contrato mínimo: só o que é comum a todos. Particularidades (ADS, rebot, FBA, Full)
ficam em métodos próprios de cada módulo, não aqui.
Credenciais de cada marketplace ficam em arquivo próprio (<nome>_config.json, no .gitignore).
"""

from abc import ABC, abstractmethod


class Marketplace(ABC):
    nome: str = ""  # identificador curto: "ml", "amazon", "shopee", "bling"

    @abstractmethod
    def contas(self) -> list[str]:
        """IDs das contas configuradas (ex.: meli01, bling01)."""

    @abstractmethod
    def autenticado(self, conta: str) -> bool:
        """True se a conta tem token utilizável."""

    @abstractmethod
    def listar_anuncios(self, conta: str) -> list[dict]:
        """Anúncios no formato comum: id, titulo, sku, preco, estoque, status."""

    @abstractmethod
    def listar_pedidos(self, conta: str, data_de: str, data_ate: str) -> list[dict]:
        """Pedidos no formato comum: id, data, status, itens[{id, sku, qtd, preco}], total."""
