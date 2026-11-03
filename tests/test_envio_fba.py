import unittest
from datetime import date

from marketplaces.amazon import envio_fba as e


class TestEnvioFba(unittest.TestCase):
    def test_dia_util_fim_de_semana_e_feriado(self):
        self.assertFalse(e.dia_util(date(2026, 11, 7)))   # sábado
        self.assertFalse(e.dia_util(date(2026, 11, 2)))   # Finados
        self.assertTrue(e.dia_util(date(2026, 11, 3)))

    def test_proximo_dia_util_pula_sabado_domingo_e_feriado(self):
        self.assertEqual(e.proximo_dia_util(date(2026, 10, 31)), date(2026, 11, 3))   # sáb -> ter (seg é Finados)
        self.assertEqual(e.proximo_dia_util(date(2026, 11, 3)), date(2026, 11, 4))

    def test_dimensoes(self):
        self.assertEqual(e.parse_dim("53x33x33"), (53.0, 33.0, 33.0))
        self.assertEqual(e.parse_dim("53,5×33×33"), (53.5, 33.0, 33.0))
        with self.assertRaises(e.ErroEnvio):
            e.parse_dim("53x33")

    def test_validar_quantidade(self):
        self.assertEqual(e.validar(None, 3, 30, 19), 90)
        self.assertEqual(e.validar(90, 3, 30, 19), 90)
        with self.assertRaises(e.ErroEnvio):
            e.validar(100, 3, 30, 19)
        with self.assertRaises(e.ErroEnvio):
            e.validar(None, 0, 30, 19)

    def test_corpo_plano_usa_prep_none(self):
        c = e.corpo_plano("KIT2MIC-FBA", 90, {"endereco": {"city": "SP"}}, "A2Q3Y263D00KWC", "teste")
        self.assertEqual(c["items"][0], {"msku": "KIT2MIC-FBA", "quantity": 90, "labelOwner": "SELLER", "prepOwner": "NONE"})

    def test_corpo_embalagem(self):
        c = e.corpo_embalagem("pg1", "KIT2MIC-FBA", 3, 30, 19.0, (53, 33, 33))
        caixa = c["packageGroupings"][0]["boxes"][0]
        self.assertEqual((caixa["quantity"], caixa["items"][0]["quantity"]), (3, 30))
        self.assertEqual(caixa["dimensions"]["unitOfMeasurement"], "CM")

    def test_escolher_opcao(self):
        def op(i, ini, sol="AMAZON_PARTNERED_CARRIER"):
            return {"transportationOptionId": i, "shippingSolution": sol, "carrierAppointment": {"startTime": ini} if ini else None}
        ops = [op("sab", "2026-11-07T12:00Z"), op("b", "2026-11-05T12:00Z"), op("a", "2026-11-04T12:00Z"),
               op("propria", "2026-11-03T12:00Z", "USE_YOUR_OWN_CARRIER"), op("sem", None)]
        self.assertEqual(e.escolher_opcao(ops, mais_cedo=True)["transportationOptionId"], "a")
        self.assertEqual(e.escolher_opcao(ops, data=date(2026, 11, 5))["transportationOptionId"], "b")
        self.assertIsNone(e.escolher_opcao(ops, data=date(2026, 11, 7)))   # sábado nunca
        self.assertIsNone(e.escolher_opcao(ops))


if __name__ == "__main__":
    unittest.main()
