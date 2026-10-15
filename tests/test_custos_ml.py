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


class MelhorOportunidade(unittest.TestCase):
    ATIVA_NOSSA = {"type": "SELLER_CAMPAIGN", "name": "19 SET a 19 OUT"}

    def test_candidata_com_rebot_rende_mais(self):
        smart = {"status": "candidate", "type": "SMART", "name": "Impulsione", "price": 119.29,
                 "meli_percentage": 1.8, "original_price": 250}
        o = ml_api.melhor_oportunidade([smart], 119.70, 250, 0.13, self.ATIVA_NOSSA)
        # atual: 119,70 - 15,56 = 104,14 | candidata: 119,29 - 15,51 + 4,50 = 108,28
        self.assertEqual(o["ganho_un"], 4.14)
        self.assertEqual(o["nome"], "Impulsione")
        self.assertFalse(o["empate"])

    def test_candidata_pior_nao_aparece(self):
        pior = {"status": "candidate", "type": "LIGHTNING", "price": 50, "meli_percentage": None,
                "original_price": 89.9}
        self.assertIsNone(ml_api.melhor_oportunidade([pior], 79.8, 89.9, 0.13, self.ATIVA_NOSSA))

    def test_empate_prefere_campanha_do_ml_sobre_a_nossa(self):
        ml = {"status": "candidate", "type": "LIGHTNING", "price": 34.99}
        o = ml_api.melhor_oportunidade([ml], 34.99, 40, 0.13, self.ATIVA_NOSSA)
        self.assertTrue(o["empate"])
        # se a atual já é do ML, empate não é oportunidade
        self.assertIsNone(ml_api.melhor_oportunidade([ml], 34.99, 40, 0.13, {"type": "SMART"}))

    def test_ignora_ja_inscrita(self):
        pend = {"status": "pending", "type": "SMART", "price": 10, "meli_percentage": 5, "original_price": 100}
        self.assertIsNone(ml_api.melhor_oportunidade([pend], 79.8, 89.9, 0.13, None))


class CampanhaEfetiva(unittest.TestCase):
    def test_pelo_id_do_sale_price(self):
        promos = [{"id": "C1", "status": "started"}, {"id": "P2", "ref_id": "P2", "status": "started"}]
        sp = {"metadata": {"promotion_id": "P2"}}
        self.assertEqual(ml_api.campanha_efetiva(promos, sp)["id"], "P2")
        self.assertIsNone(ml_api.campanha_efetiva(promos, {"metadata": {}}))


if __name__ == "__main__":
    unittest.main()
