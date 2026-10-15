"""Rentabilidade Amazon: agregação do Finances e margem por SKU (sem rede)."""
import unittest
from unittest import mock

from marketplaces.amazon import api, financeiro, rentabilidade


def brl(v):
    return {"CurrencyCode": "BRL", "CurrencyAmount": v}


EVENTOS = [{
    "ShipmentEventList": [{"ShipmentItemList": [{
        "SellerSKU": "A-FBA", "QuantityShipped": 10,
        "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": brl(1000.0)},
                           {"ChargeType": "Tax", "ChargeAmount": brl(90.0)}],
        "ItemFeeList": [{"FeeType": "Commission", "FeeAmount": brl(-120.0)},
                        {"FeeType": "FBAPerUnitFulfillmentFee", "FeeAmount": brl(-130.0)},
                        {"FeeType": "AmazonForAllFee", "FeeAmount": brl(-10.0)}],
        "PromotionList": [{"PromotionAmount": brl(-50.0)}]}]}],
    "RefundEventList": [{"ShipmentItemAdjustmentList": [{
        "SellerSKU": "A-FBA", "QuantityShipped": -2,
        "ItemChargeAdjustmentList": [{"ChargeType": "Principal", "ChargeAmount": brl(-200.0)},
                                     {"ChargeType": "Tax", "ChargeAmount": brl(-18.0)}],
        "ItemFeeAdjustmentList": [{"FeeType": "Commission", "FeeAmount": brl(24.0)}],
        "PromotionAdjustmentList": [{"PromotionAmount": brl(10.0)}]}]}],
    "ServiceFeeEventList": [{"FeeList": [{"FeeReason": "FBAStorageFee", "FeeAmount": brl(-30.0)}]}],
    "ProductAdsPaymentEventList": [{"transactionValue": brl(-100.0)}],
    "AdjustmentEventList": [{"AdjustmentType": "WAREHOUSE_DAMAGE", "AdjustmentAmount": brl(5.0)}],
    "SellerDealPaymentEventList": [{"x": 1}],
}]


class Agregar(unittest.TestCase):
    def setUp(self):
        self.ag = financeiro.agregar(EVENTOS)
        self.s = self.ag["skus"]["A-FBA"]

    def test_imposto_repassado_nao_e_receita(self):
        self.assertEqual(self.s["receita"], 1000.0)
        self.assertEqual(self.s["receita_estornada"], -200.0)

    def test_taxas_por_tipo_e_estornos(self):
        self.assertEqual(self.s["comissao"], -120.0)
        self.assertEqual(self.s["comissao_estornada"], 24.0)
        self.assertEqual(self.s["taxa_fba"], -130.0)
        self.assertEqual(self.s["outras_taxas"], -10.0)

    def test_promocoes_e_unidades(self):
        self.assertEqual(self.s["promocoes"], -50.0)
        self.assertEqual(self.s["promocoes_estornadas"], 10.0)
        self.assertEqual((self.s["un_enviadas"], self.s["un_devolvidas"]), (10, 2))

    def test_custos_da_conta_ficam_fora_do_sku(self):
        c = self.ag["conta"]
        self.assertEqual(c["ads"], -100.0)
        self.assertEqual(c["servico"], {"FBAStorageFee": -30.0})
        self.assertEqual(c["ajustes"], {"WAREHOUSE_DAMAGE": 5.0})

    def test_lista_desconhecida_e_sinalizada(self):
        self.assertEqual(self.ag["nao_tratadas"], {"SellerDealPaymentEventList": 1})


class Margem(unittest.TestCase):
    def setUp(self):
        self.a = financeiro.agregar(EVENTOS)["skus"]["A-FBA"]

    def test_margem_completa(self):
        m = rentabilidade.margem_sku(self.a, {"cmv_unit": 30.0, "imposto_pct": 0.10})
        # receita líquida = 1000 - 200 - 50 + 10 = 760
        self.assertEqual(m["receita_liquida"], 760.0)
        # após taxas = 760 - 120 + 24 - 130 - 10 = 524
        self.assertEqual(m["margem_apos_taxas"], 524.0)
        self.assertEqual(m["imposto"], -76.0)            # 10% de 760
        self.assertEqual(m["cmv"], -240.0)               # 30 x (10 - 2)
        self.assertEqual(m["margem"], 208.0)
        self.assertEqual(m["margem_pct"], 27.37)
        self.assertTrue(m["completo"])

    def test_sem_custo_fica_incompleto(self):
        m = rentabilidade.margem_sku(self.a, None)
        self.assertFalse(m["completo"])
        self.assertIsNone(m["margem"])
        self.assertEqual(m["margem_apos_taxas"], 524.0)

    def test_dre_soma_skus_e_custos_da_conta(self):
        d = rentabilidade.dre(financeiro.agregar(EVENTOS),
                              {"A-FBA": {"cmv_unit": 30.0, "imposto_pct": 0.10}})["dre"]
        self.assertEqual(d["margem_contribuicao"], 208.0)
        self.assertEqual(d["conta"]["total"], -125.0)    # ads -100, armazenagem -30, ajuste +5
        self.assertEqual(d["resultado"], 83.0)

    def test_resultado_nao_e_calculado_se_faltar_custo(self):
        d = rentabilidade.dre(financeiro.agregar(EVENTOS), {})["dre"]
        self.assertIsNone(d["resultado"])


class Buscar(unittest.TestCase):
    def test_pagina_com_erro_aborta(self):
        respostas = [(200, {"payload": {"FinancialEvents": {}, "NextToken": "t"}}), (500, {"errors": []})]
        with mock.patch.object(api, "get", side_effect=respostas), mock.patch("time.sleep"):
            with self.assertRaises(api.ErroAmazon):
                financeiro.buscar("amazon02", "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")

    def test_junta_as_paginas(self):
        respostas = [(200, {"payload": {"FinancialEvents": {"a": 1}, "NextToken": "t"}}),
                     (200, {"payload": {"FinancialEvents": {"b": 2}}})]
        with mock.patch.object(api, "get", side_effect=respostas), mock.patch("time.sleep"):
            self.assertEqual(len(financeiro.buscar("amazon02", "x", "y")), 2)


if __name__ == "__main__":
    unittest.main()
