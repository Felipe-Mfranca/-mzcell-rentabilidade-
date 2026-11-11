"""Bipagem da etiqueta -> pedido no Bling — sem rede."""
import unittest
from unittest import mock

from marketplaces.expedicao import bipagem, ml

PEDIDOS = {"bling01": {}, "bling02": {"2000015869601391": {"numero": 11642, "numeroLoja": "2000015869601391", "situacao": {"id": 6}}},
           "bling03": {"SHOPEE123": {"numero": 900, "numeroLoja": "SHOPEE123", "situacao": {"id": 24}}}}


class TestBipagem(unittest.TestCase):
    def setUp(self):
        def falso_get(b, path, **params):
            return 200, {"data": [p for k, p in PEDIDOS[b].items() if k in params["numerosLojas[]"]]}
        self.p = mock.patch.object(bipagem.bling, "get", falso_get)
        self.p.start()
        self.cli = mock.patch.object(bipagem, "_cliente_ml", lambda conta, n: {"apelido": "COMPRADOR1", "etiqueta": 48431307475} if conta == "meli02" else {})
        self.cli.start()

    def tearDown(self):
        self.p.stop()
        self.cli.stop()

    def test_qr_do_flex_vira_o_envio(self):
        self.assertEqual(bipagem.normalizar('{"id":"48431307475","t":"lm"}'), "48431307475")

    def test_qr_do_flex_com_aspas_trocadas_pelo_teclado(self):
        sujo = 'Ç~id~Ç~48431307475~,~t~Ç~lm~,~sender_id~Ç1874506744,~hash_code~Ç~abc123=~}'
        self.assertEqual(bipagem.normalizar(sujo), "48431307475")
        self.assertEqual(bipagem.normalizar('{"sender_id":1874506744,"id":"48431307475"}'), "48431307475")

    def test_conta_selecionada_so_procura_no_bling_dela(self):
        self.assertFalse(bipagem.buscar("2000015869601391", "meli01")["encontrado"])
        self.assertEqual(bipagem.buscar("2000015869601391", "meli02")["numero"], 11642)
        with self.assertRaises(ValueError):
            bipagem.buscar("1", "meli09")

    def test_pack_direto_no_bling(self):
        r = bipagem.buscar("2000015869601391")
        self.assertEqual((r["bling_n"], r["numero"], r["situacao"]), (2, 11642, "Em aberto"))

    def test_envio_do_ml_pelo_mapa_em_memoria(self):
        linha = {"envio": 48431307475, "conta": "meli02", "pedido": 2000019345841910, "pacote": 2000015869601391}
        with mock.patch.dict(ml._cache, {("x",): (0, {"linhas": [linha]})}, clear=True):
            r = bipagem.buscar("48431307475")
        self.assertEqual((r["bling"], r["numero"]), ("bling02", 11642))

    def test_pedido_do_ml_traz_apelido_e_etiqueta(self):
        r = bipagem.buscar("2000015869601391")
        self.assertEqual((r["apelido"], r["etiqueta"]), ("COMPRADOR1", 48431307475))
        self.assertNotIn("apelido", bipagem.buscar("SHOPEE123"))                  # Shopee no Bling 3: sem apelido do ML

    def test_cliente_ml_pelo_pacote(self):
        class Cli:
            def get(self, path):
                return {"/orders/200": (404, {}), "/packs/200": (200, {"orders": [{"id": 7}]}),
                        "/orders/7": (200, {"buyer": {"nickname": "FULANO"}, "shipping": {"id": 999}})}[path]
        self.cli.stop()
        with mock.patch.object(bipagem.ml, "ML", lambda conta: Cli()):
            self.assertEqual(bipagem._cliente_ml("meli01", "200"), {"apelido": "FULANO", "etiqueta": 999})
            self.assertEqual(bipagem._cliente_ml("shopee", "200"), {})
        self.cli.start()

    def test_outro_marketplace_pelo_numero_da_loja(self):
        self.assertEqual(bipagem.buscar("shopee123")["encontrado"], False)   # numeroLoja é exato
        self.assertEqual(bipagem.buscar("SHOPEE123")["bling_n"], 3)


if __name__ == "__main__":
    unittest.main()
