"""Bipagem da etiqueta -> pedido no Bling — sem rede."""
import unittest
from unittest import mock

from marketplaces.expedicao import bipagem, ml

PEDIDOS = {"bling01": {}, "bling02": {"2000015869601391": {"numero": 11642, "numeroLoja": "2000015869601391", "situacao": {"id": 6}}},
           "bling03": {"SHOPEE123": {"numero": 900, "numeroLoja": "SHOPEE123", "situacao": {"id": 24}}}}


class TestBipagem(unittest.TestCase):
    def setUp(self):
        self.p = mock.patch.object(bipagem.bling_checkout, "_pedidos", lambda b: PEDIDOS[b])
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_qr_do_flex_vira_o_envio(self):
        self.assertEqual(bipagem.normalizar('{"id":"48431307475","t":"lm"}'), "48431307475")

    def test_pack_direto_no_bling(self):
        r = bipagem.buscar("2000015869601391")
        self.assertEqual((r["bling_n"], r["numero"], r["situacao"]), (2, 11642, "Em aberto"))

    def test_envio_do_ml_pelo_mapa_em_memoria(self):
        linha = {"envio": 48431307475, "conta": "meli02", "pedido": 2000019345841910, "pacote": 2000015869601391}
        with mock.patch.dict(ml._cache, {("x",): (0, {"linhas": [linha]})}, clear=True):
            r = bipagem.buscar("48431307475")
        self.assertEqual((r["bling"], r["numero"]), ("bling02", 11642))

    def test_outro_marketplace_pelo_numero_da_loja(self):
        self.assertEqual(bipagem.buscar("shopee123")["encontrado"], False)   # numeroLoja é exato
        self.assertEqual(bipagem.buscar("SHOPEE123")["bling_n"], 3)


if __name__ == "__main__":
    unittest.main()
