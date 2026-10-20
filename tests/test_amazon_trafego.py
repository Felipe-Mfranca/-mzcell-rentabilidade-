"""Visitas e conversão da Amazon (Sales and Traffic): extração e cache, sem rede."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

from marketplaces.amazon import api, painel

RELATORIO = {"salesAndTrafficByAsin": [
    {"parentAsin": "B0P", "childAsin": "B01", "salesByAsin": {"unitsOrdered": 14, "orderedProductSales": {"amount": 448.7}},
     "trafficByAsin": {"sessions": 44, "pageViews": 62, "unitSessionPercentage": 31.82, "buyBoxPercentage": 100.0}},
    {"childAsin": "B02", "salesByAsin": {}, "trafficByAsin": {}}]}


class Extracao(unittest.TestCase):
    def test_extrai_campos_por_asin(self):
        r = painel.extrair_trafego(RELATORIO)
        self.assertEqual(r["B01"], {"sessoes": 44, "visualizacoes": 62, "conversao_pct": 31.82, "buybox_pct": 100.0,
                                    "unidades_pedidas": 14, "vendas_pedidas": 448.7})
        self.assertEqual(r["B02"]["sessoes"], 0)


class Cache(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        p = mock.patch.object(painel, "_arq_trafego", return_value=os.path.join(self.d.name, "t.json"))
        p.start()
        self.addCleanup(p.stop)
        painel._trafego_estado.clear()

    def test_baixar_grava_cache_e_trafego_le(self):
        with mock.patch.object(painel.relatorios, "criar", return_value="r1") as c, \
             mock.patch.object(painel.relatorios, "aguardar", return_value="doc"), \
             mock.patch.object(painel.relatorios, "baixar_texto", return_value=json.dumps(RELATORIO)):
            painel._baixar_trafego("amazon02", "2026-10-01", "2026-10-07")
        self.assertEqual(c.call_args.args[3], "2026-10-08T00:00:00Z")   # fim exclusivo: dia seguinte
        t = painel.trafego("amazon02", "2026-10-01", "2026-10-07")
        self.assertTrue(t["disponivel"])
        self.assertEqual(t["asins"]["B01"]["sessoes"], 44)

    def test_falha_do_relatorio_vira_erro_sem_derrubar(self):
        with mock.patch.object(painel.relatorios, "criar", side_effect=api.ErroAmazon("limite", 429)):
            painel._baixar_trafego("amazon02", "2026-10-01", "2026-10-07")
        t = painel.trafego("amazon02", "2026-10-01", "2026-10-07")
        self.assertFalse(t["disponivel"])
        self.assertIn("limite", t["erro"])

    def test_periodo_recente_vence_depois_de_12h(self):
        ontem = (datetime.now(painel.BRT).date() - timedelta(days=1)).isoformat()
        velho = (datetime.now(painel.BRT) - timedelta(hours=13)).isoformat(timespec="seconds")
        json.dump({"periodos": {f"{ontem}|{ontem}": {"gerado_em": velho, "asins": {}}}}, open(painel._arq_trafego("x"), "w"))
        self.assertFalse(painel.trafego("amazon02", ontem, ontem)["disponivel"])
        json.dump({"periodos": {"2026-01-01|2026-01-07": {"gerado_em": velho, "asins": {}}}}, open(painel._arq_trafego("x"), "w"))
        self.assertTrue(painel.trafego("amazon02", "2026-01-01", "2026-01-07")["disponivel"])   # período antigo não vence

    def test_limite_de_dias(self):
        with self.assertRaises(api.ErroAmazon):
            painel._validar_trafego("2026-08-20", "2026-10-18")


if __name__ == "__main__":
    unittest.main()
