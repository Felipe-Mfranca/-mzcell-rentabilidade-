"""SKU do anúncio (chave para o Bling): sem rede."""
import unittest

import ml_api


class SkuAnuncio(unittest.TestCase):
    def test_seller_custom_field_tem_prioridade(self):
        self.assertEqual(ml_api.sku_do_anuncio({"seller_custom_field": " ABC ", "attributes": [{"id": "SELLER_SKU", "value_name": "X"}]}), "ABC")

    def test_atributo_seller_sku(self):
        self.assertEqual(ml_api.sku_do_anuncio({"seller_custom_field": None, "attributes": [{"id": "SELLER_SKU", "value_name": "controle-ps4m1"}]}), "controle-ps4m1")

    def test_variacoes_distintas_juntas(self):
        it = {"variations": [{"seller_custom_field": "A"}, {"attributes": [{"id": "SELLER_SKU", "value_name": "B"}]}, {"seller_custom_field": "A"}]}
        self.assertEqual(ml_api.sku_do_anuncio(it), "A / B")

    def test_sem_sku(self):
        self.assertEqual(ml_api.sku_do_anuncio({"attributes": [{"id": "COLOR", "value_name": "Preto"}]}), "")


if __name__ == "__main__":
    unittest.main()
