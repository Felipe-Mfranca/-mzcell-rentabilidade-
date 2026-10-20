"""Painel Amazon: agregação por dia, soma de período e detalhes de SKU, sem rede."""
import json
import os
import tempfile
import unittest
from unittest import mock

from marketplaces.amazon import financeiro, painel, rentabilidade


def envio(data, sku, un, preco, comissao, fba):
    return {"PostedDate": data, "ShipmentItemList": [{
        "SellerSKU": sku, "QuantityShipped": un,
        "ItemChargeList": [{"ChargeType": "Principal", "ChargeAmount": {"CurrencyAmount": preco}},
                           {"ChargeType": "Tax", "ChargeAmount": {"CurrencyAmount": 99.0}}],
        "ItemFeeList": [{"FeeType": "Commission", "FeeAmount": {"CurrencyAmount": -comissao}},
                        {"FeeType": "FBAPerUnitFulfillmentFee", "FeeAmount": {"CurrencyAmount": -fba}}],
        "PromotionList": []}]}


PAGINAS = [{"ShipmentEventList": [envio("2026-10-10T15:00:00Z", "S1", 2, 100.0, 15.0, 10.0),
                                  envio("2026-10-11T15:00:00Z", "S1", 1, 50.0, 7.5, 5.0),
                                  envio("2026-10-11T16:00:00Z", "S2", 1, 80.0, 12.0, 8.0)],
            "ServiceFeeEventList": [{"PostedDate": "2026-10-11T10:00:00Z", "FeeList": [{"FeeReason": "Subscription", "FeeAmount": {"CurrencyAmount": -19.0}}]}]}]


class PorDia(unittest.TestCase):
    def test_reparte_por_dia_de_brasilia_e_ignora_imposto_repassado(self):
        r = financeiro.agregar_por_dia(PAGINAS)
        self.assertEqual(sorted(r["skus"]["S1"]), ["2026-10-10", "2026-10-11"])
        self.assertEqual(r["skus"]["S1"]["2026-10-10"]["receita"], 100.0)   # o Tax de 99 não entra
        self.assertEqual(r["conta"]["2026-10-11"]["servico"], {"Subscription": -19.0})

    def test_data_perto_da_meia_noite_usa_brasilia(self):
        p = [{"ShipmentEventList": [envio("2026-10-11T02:30:00Z", "S1", 1, 10.0, 1.0, 1.0)]}]   # 23:30 do dia 10 em Brasília
        self.assertEqual(list(financeiro.agregar_por_dia(p)["skus"]["S1"]), ["2026-10-10"])

    def test_soma_do_periodo_bate_com_agregar(self):
        por_dia = financeiro.agregar_por_dia(PAGINAS)
        tot = financeiro.somar_periodo(por_dia, "2026-10-10", "2026-10-11")
        ref = financeiro.agregar(PAGINAS)
        self.assertEqual(tot["skus"]["S1"]["receita"], ref["skus"]["S1"]["receita"])
        self.assertEqual(tot["skus"]["S1"]["comissao"], ref["skus"]["S1"]["comissao"])
        parcial = financeiro.somar_periodo(por_dia, "2026-10-11", "2026-10-11")
        self.assertEqual(parcial["skus"]["S1"]["un_enviadas"], 1)
        self.assertEqual(parcial["conta"]["servico"], {"Subscription": -19.0})
        self.assertNotIn("S1", financeiro.somar_periodo(por_dia, "2026-09-01", "2026-09-30")["skus"])


class Painel(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        cache = {"gerado_em": "2026-10-19T10:00:00", "de": "2026-08-20", "ate": "2026-10-19",
                 "por_dia": financeiro.agregar_por_dia(PAGINAS), "titulos": {"S1": {"titulo": "Produto 1", "asin": "B01"}}}
        self.arq = os.path.join(self.d.name, "cache.json")
        json.dump(cache, open(self.arq, "w", encoding="utf-8"))
        for alvo, valor in ((painel, "_caminho"),):
            p = mock.patch.object(painel, valor, return_value=self.arq)
            p.start()
            self.addCleanup(p.stop)
        c = mock.patch.object(rentabilidade, "carregar_custos", return_value={"S1": {"cmv_unit": 20.0, "imposto_pct": 0.10}})
        c.start()
        self.addCleanup(c.stop)
        b = mock.patch("cmv_base.carregar", return_value={"itens": {}})
        b.start()
        self.addCleanup(b.stop)
        a = mock.patch("cmv_base.aliquotas_da_conta", return_value=(0.1072, 0.1487))
        a.start()
        self.addCleanup(a.stop)

    def test_painel_calcula_margem_e_periodo_anterior(self):
        r = painel.painel("amazon02", "2026-10-11", "2026-10-11")
        s1 = next(x for x in r["skus"] if x["sku"] == "S1")
        # 1 un: receita 50, comissão -7,5, FBA -5 => após taxas 37,5; imposto 10% de 50 = -5; CMV -20 => margem 12,5
        self.assertEqual((s1["receita_liquida"], s1["margem_apos_taxas"], s1["imposto"], s1["cmv"], s1["margem"]), (50.0, 37.5, -5.0, -20.0, 12.5))
        self.assertEqual(s1["titulo"], "Produto 1")
        self.assertEqual(r["anterior"], {"de": "2026-10-10", "ate": "2026-10-10"})
        self.assertEqual(s1["receita_liquida_anterior"], 100.0)

    def test_sku_sem_custo_fica_incompleto(self):
        r = painel.painel("amazon02", "2026-10-10", "2026-10-11")
        s2 = next(x for x in r["skus"] if x["sku"] == "S2")
        self.assertFalse(s2["completo"])

    def test_detalhe_do_sku(self):
        d = painel.sku_detalhe("amazon02", "S1", "2026-10-10", "2026-10-11")
        self.assertEqual(d["atual"]["un_liquidas"], 3)
        self.assertEqual(d["aliquotas"], {"com_st": 0.1072, "sem_st": 0.1487})
        self.assertEqual(d["custo"]["cmv_unit"], 20.0)

    def test_periodo_invalido_e_sem_cache(self):
        from marketplaces.amazon import api
        with self.assertRaises(api.ErroAmazon):
            painel.painel("amazon02", "2026-10-11", "2026-10-01")
        with self.assertRaises(api.ErroAmazon):
            painel.painel("amazon02", "2025-01-01", "2026-10-01")
        with mock.patch.object(painel, "carregar_cache", return_value=None):
            with self.assertRaises(api.ErroAmazon) as c:
                painel.painel("amazon02", "2026-10-10", "2026-10-11")
            self.assertEqual(c.exception.status, 409)


if __name__ == "__main__":
    unittest.main()
