import unittest
from datetime import date

import clonar_campanha as c


class TestModeloClone(unittest.TestCase):
    def test_proximo_periodo_30_dias(self):
        self.assertEqual(c.proximo_periodo(date(2026, 10, 27)), (date(2026, 10, 28), date(2026, 11, 26)))

    def test_proximo_periodo_limita_em_30(self):
        self.assertEqual(c.proximo_periodo(date(2026, 10, 27), 60)[1], date(2026, 11, 26))

    def test_fim_em_utc_vira_dia_anterior(self):
        self.assertEqual(c.fim_da_campanha({"finish_date": "2026-10-28T02:59:59Z"}), date(2026, 10, 27))
        self.assertEqual(c.fim_da_campanha({"finish_date": "2026-10-27T23:59:59"}), date(2026, 10, 27))

    def test_periodo_gerado_passa_na_validacao(self):
        ini, fim = c.proximo_periodo(date(2026, 10, 27))
        self.assertEqual(c.validar_datas(ini, fim, date(2026, 10, 27)), 30)


if __name__ == "__main__":
    unittest.main()
