"""Expedição do galpão (ML), sem rede."""
import unittest

from marketplaces.expedicao import ml


class Prazos(unittest.TestCase):
    def test_flex_meta_e_12h_do_dia_do_prazo(self):
        prazo, meta = ml.prazos("Flex", "2026-10-19T23:00:00-03:00")
        self.assertEqual(meta, "2026-10-19T12:00:00-03:00")
        self.assertEqual(prazo, "2026-10-19T23:00:00-03:00")

    def test_flex_prazo_antes_das_12h_vale_o_prazo(self):
        self.assertEqual(ml.prazos("Flex", "2026-10-19T10:00:00-03:00")[1], "2026-10-19T10:00:00-03:00")

    def test_agencia_e_coleta_preparo_30_min_antes(self):
        self.assertEqual(ml.prazos("Agência", "2026-10-19T12:00:00-03:00")[1], "2026-10-19T11:30:00-03:00")
        self.assertEqual(ml.prazos("Coleta", "2026-10-21T14:15:00-03:00")[1], "2026-10-21T13:45:00-03:00")

    def test_utc_vira_brasilia(self):
        self.assertEqual(ml.prazos("Agência", "2026-10-19T15:00:00Z")[1], "2026-10-19T11:30:00-03:00")

    def test_sem_data(self):
        self.assertEqual(ml.prazos("Flex", None), (None, None))


class Linha(unittest.TestCase):
    ped = {"id": 1, "order_items": [{"quantity": 2, "item": {"id": "MLB1", "title": "x", "seller_sku": "S"}}]}

    def test_full_e_enviado_ficam_fora(self):
        self.assertIsNone(ml.linha("meli01", self.ped, {"logistic_type": "fulfillment", "status": "ready_to_ship"}, {}))
        self.assertIsNone(ml.linha("meli01", self.ped, {"logistic_type": "self_service", "status": "shipped"}, {}))

    def test_flex_pronto(self):
        r = ml.linha("meli01", self.ped, {"id": 9, "logistic_type": "self_service", "status": "ready_to_ship",
                                          "substatus": "ready_to_print"}, {"expected_date": "2026-10-19T23:00:00-03:00", "status": "on_time"})
        self.assertEqual((r["canal"], r["liberado"], r["itens"][0]["qtd"]), ("Flex", True, 2))

    def test_buffered_nao_esta_liberado(self):
        r = ml.linha("meli02", self.ped, {"id": 9, "logistic_type": "xd_drop_off", "status": "pending", "substatus": "buffered"},
                     {"expected_date": "2026-10-20T23:59:59-03:00"})
        self.assertFalse(r["liberado"])


if __name__ == "__main__":
    unittest.main()
