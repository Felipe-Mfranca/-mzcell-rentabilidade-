"""DBA da Amazon: o dia do pedido vale o prazo de envio informado pela Amazon (LatestShipDate) — sem rede."""
import unittest
from datetime import date, datetime, timedelta, timezone

from marketplaces.expedicao import amazon_dba as a
from marketplaces.expedicao import dia as d

BRT = timezone(timedelta(hours=-3))
BASE = {"AmazonOrderId": "702-0000000-0000001", "FulfillmentChannel": "MFN", "OrderStatus": "Unshipped",
        "EasyShipShipmentStatus": "PendingSchedule", "NumberOfItemsUnshipped": 1}


class TestPrazoPrometido(unittest.TestCase):
    def test_caso_real_comprado_apos_11h_com_prazo_no_mesmo_dia(self):
        # compra 03/11 11:19 (após as 11:00), a Amazon pede envio até 03/11 23:59: vale 03/11, não 04/11
        o = dict(BASE, PurchaseDate="2026-11-03T14:19:00Z", LatestShipDate="2026-11-04T02:59:00Z")
        ln = a.linha("amazon02", o)
        self.assertEqual(ln["meta"], "2026-11-03T11:00:00-03:00")
        self.assertEqual(ln["ponto_ate"], "2026-11-03T14:00:00-03:00")
        self.assertEqual(ln["prazo_ml"], "2026-11-03T23:59:00-03:00")
        self.assertEqual(ln["prazo_origem"], "amazon")

    def test_pedido_de_ontem_pendente_vira_atrasado_no_painel(self):
        o = dict(BASE, PurchaseDate="2026-11-03T14:19:00Z", LatestShipDate="2026-11-04T02:59:00Z")
        agora = datetime(2026, 11, 4, 11, 10, tzinfo=BRT)
        r = d.montar([a.linha("amazon02", o)], {}, [], agora, date(2026, 11, 4))
        self.assertEqual(r["herdados"], 1)                      # prazo de dia anterior sem checkout: alerta vermelho
        self.assertEqual(next(c for c in r["cartoes"] if c["canal"] == "DBA")["pedidos"], 0)

    def test_prazo_no_dia_seguinte_vale_o_dia_seguinte(self):
        o = dict(BASE, PurchaseDate="2026-11-04T15:00:00Z", LatestShipDate="2026-11-06T02:59:00Z")   # prazo 05/11 23:59 (BRT)
        ln = a.linha("amazon01", o)
        self.assertEqual(ln["meta"], "2026-11-05T11:00:00-03:00")

    def test_sem_data_da_amazon_cai_na_regra_das_11h(self):
        o = dict(BASE, PurchaseDate="2026-11-03T14:19:00Z")      # 11:19 BRT, sem LatestShipDate
        ln = a.linha("amazon02", o)
        self.assertEqual(ln["meta"], "2026-11-04T11:00:00-03:00")
        self.assertEqual(ln["prazo_origem"], "regra 11h")


if __name__ == "__main__":
    unittest.main()
