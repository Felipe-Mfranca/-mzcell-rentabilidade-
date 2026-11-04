"""Várias caixas diferentes no mesmo envio FBA — sem rede."""
import unittest

from marketplaces.amazon import envio_fba as e


class TestCaixas(unittest.TestCase):
    def test_parse_caixa_simples_e_mista(self):
        c = e.parse_caixa("2;57x33x33;4,2;Control-Gamer-BT=10,KIT3-ESCVCANT-FBA=15")
        self.assertEqual(c["n"], 2)
        self.assertEqual(c["dim"], (57.0, 33.0, 33.0))
        self.assertEqual(c["peso"], 4.2)
        self.assertEqual(c["itens"], [("Control-Gamer-BT", 10), ("KIT3-ESCVCANT-FBA", 15)])

    def test_totais_somam_caixas_de_tipos_diferentes(self):
        specs = [e.parse_caixa("2;57x33x33;4.2;A=10,B=15"), e.parse_caixa("3;57x33x33;2.7;C=6"), e.parse_caixa("1;53x33x33;22;A=30,B=50")]
        self.assertEqual(e.totais_por_sku(specs), {"A": 50, "B": 80, "C": 18})

    def test_corpo_multi_tem_uma_entrada_por_tipo_de_caixa(self):
        specs = [e.parse_caixa("2;57x33x33;4.2;A=10"), e.parse_caixa("1;53x33x33;19;B=30")]
        corpo = e.corpo_embalagem_multi("pg1", specs)
        caixas = corpo["packageGroupings"][0]["boxes"]
        self.assertEqual([c["quantity"] for c in caixas], [2, 1])
        self.assertEqual(caixas[1]["weight"], {"unit": "KG", "value": 19.0})
        self.assertEqual(caixas[1]["items"][0]["msku"], "B")

    def test_entradas_invalidas(self):
        for ruim in ("x;57x33x33;4;A=1", "2;57x33;4;A=1", "2;57x33x33;0;A=1", "2;57x33x33;4;A"):
            with self.assertRaises(e.ErroEnvio):
                e.parse_caixa(ruim)


if __name__ == "__main__":
    unittest.main()
