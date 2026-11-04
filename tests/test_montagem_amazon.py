"""Pré-carga dos Fulls da Amazon na fila de montagem — sem rede (Amazon simulada)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from marketplaces.montagem import amazon_fba as af
from marketplaces.montagem import armazenamento as arm

BRT = timezone(timedelta(hours=-3))
AGORA = datetime(2026, 11, 4, 10, 0, tzinfo=BRT)


class FalsaAmazon:
    """Responde como a SP-API para dois envios: um completo (NF + coleta) e outro sem NF."""
    PLANOS = {
        "wfA": {"name": "MZ 4 SKUs", "shipments": [{"shipmentId": "shA", "status": "WORKING"}]},
        "wfB": {"name": "Sem NF", "shipments": [{"shipmentId": "shB", "status": "WORKING"}]},
        "wfC": {"name": "Coleta antiga", "shipments": [{"shipmentId": "shC", "status": "SHIPPED"}]},
    }

    def get(self, conta, caminho, **kw):
        if caminho.endswith("/inboundPlans"):
            return 200, {"inboundPlans": [{"inboundPlanId": k, "status": "ACTIVE", "createdAt": "2026-11-03T10:00:00Z"} for k in self.PLANOS]}
        for k, p in self.PLANOS.items():
            if caminho.endswith(f"/inboundPlans/{k}"):
                return 200, p
            if caminho.endswith(f"/inboundPlans/{k}/items"):
                return 200, {"items": [{"msku": "2MIC-FBA", "quantity": 30, "asin": "B0"}, {"msku": "CONTROLPC-FBA", "quantity": 20}]}
            if caminho.endswith(f"/inboundPlans/{k}/transportationOptions"):
                assert kw.get("pageSize", 20) <= 20          # limite real da Amazon (acima disso: HTTP 400)
                inicio = "2026-10-20T13:00:00Z" if k == "wfC" else "2026-11-06T13:00:00Z"
                return 200, {"transportationOptions": [{"transportationOptionId": "t1", "carrier": {"name": "Total Express"},
                                                        "carrierAppointment": {"startTime": inicio}}]}
        if "/shipments/" in caminho:
            sid = caminho.rsplit("/", 1)[1]
            return 200, {"shipmentConfirmationId": "FBA" + sid[-1] * 3, "selectedTransportationOptionId": "t1",
                         "placementOptionId": "pl", "destination": {"warehouseId": "GRU8"}}
        if caminho.endswith("governmentInvoiceRequests"):
            return (200, {"status": "SUCCESS"}) if kw["shipmentId"] == "shA" else (404, {})
        return 404, {}


class TestAmazonFBA(unittest.TestCase):
    def setUp(self):
        self.orig = af.api.get
        af.api.get = FalsaAmazon().get
        af._cache.update(ts=0.0, dados=None)
        self.dir = tempfile.mkdtemp()
        arm.CAMINHO = os.path.join(self.dir, "m.json")

    def tearDown(self):
        af.api.get = self.orig

    def test_so_importa_o_que_tem_nf_e_coleta(self):
        r = af.buscar(contas=("amazon01",), agora=AGORA)
        por = {x["numero"]: x for x in r["envios"]}
        self.assertTrue(por["FBAAAA"]["pronto"])
        self.assertEqual(por["FBAAAA"]["coleta"], "2026-11-06")
        self.assertEqual(por["FBAAAA"]["prazo"], "2026-11-06T09:00")           # coleta 10:00 BRT menos 1 h
        self.assertEqual([(p["sku"], p["unidades"]) for p in por["FBAAAA"]["produtos"]], [("2MIC-FBA", 30), ("CONTROLPC-FBA", 20)])
        self.assertFalse(por["FBABBB"]["pronto"])
        self.assertIn("NF", por["FBABBB"]["motivo"])
        self.assertNotIn("FBACCC", por)                                          # coleta antiga: ignorada mesmo como SHIPPED

    def test_cria_na_fila_sem_responsaveis(self):
        r = af.buscar(contas=("amazon01",), agora=AGORA)
        x = next(e for e in r["envios"] if e["pronto"])
        env = arm.criar(x["numero"], x["conta"], x["coleta"], x["prazo"], x["produtos"], "teste", "amazon")
        self.assertEqual(env["chave"], "amazon-FBAAAA")
        self.assertTrue(all(p["estoquista"] == "" and p["embalador"] == "" for p in env["produtos"]))

    def test_cache_evita_reler_a_amazon(self):
        chamadas = []

        def leitor():
            chamadas.append(1)
            return {"envios": [], "erros": {}, "gerado_em": "x"}

        af.disponiveis(leitor=leitor); af.disponiveis(leitor=leitor)
        self.assertEqual(len(chamadas), 1)
        af.disponiveis(atualizar=True, leitor=leitor)
        self.assertEqual(len(chamadas), 2)


if __name__ == "__main__":
    unittest.main()
