"""Regras puras dos custos reais do simulador (sem rede)."""
import unittest

import ml_api


class EscolherCampanha(unittest.TestCase):
    def test_sem_campanha_em_andamento(self):
        promos = [{"status": "candidate", "price": 50}, {"status": "pending", "price": 50}]
        self.assertIsNone(ml_api.escolher_campanha(promos, 50))

    def test_ignora_preco_zero(self):
        self.assertIsNone(ml_api.escolher_campanha([{"status": "started", "price": 0}], 50))

    def test_escolhe_a_do_preco_praticado(self):
        nossa = {"id": "C", "status": "started", "price": 119.7}
        smart = {"id": "S", "status": "started", "price": 119.29}
        self.assertEqual(ml_api.escolher_campanha([nossa, smart], 119.29)["id"], "S")
        self.assertEqual(ml_api.escolher_campanha([nossa, smart], 119.70)["id"], "C")


class RebotUnitario(unittest.TestCase):
    def test_percentual_sobre_preco_cheio_da_campanha(self):
        # SMART 1,8% sobre R$250 -> R$4,50 (pedido real: tarifa cheia - sale_fee = 4,47)
        self.assertEqual(ml_api.rebot_unitario({"meli_percentage": 1.8, "original_price": 250}), 4.5)

    def test_sem_rebot(self):
        self.assertEqual(ml_api.rebot_unitario(None), 0.0)
        self.assertEqual(ml_api.rebot_unitario({"meli_percentage": None, "original_price": 89.9}), 0.0)
        self.assertEqual(ml_api.rebot_unitario({"meli_percentage": 3, "original_price": 0}), 0.0)


if __name__ == "__main__":
    unittest.main()
