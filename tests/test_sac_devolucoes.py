"""SAC: devoluções pela logística reversa do ML — sem rede."""
import os
import tempfile
import threading
import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from marketplaces.sac import devolucoes as dv
from marketplaces.sac.router import router


def ret(status, envio=None):
    return {"status": status, "subtype": "return_total", "status_money": "refunded",
            "shipments": [{"status": envio, "tracking_number": "MEL1"}] if envio else []}


class TestFase(unittest.TestCase):
    def test_fases(self):
        self.assertEqual(dv.fase_da_devolucao(ret("delivered", "delivered")), "chegou")
        self.assertEqual(dv.fase_da_devolucao(ret("shipped", "shipped")), "a_caminho")
        # postada na agência: a devolução está "shipped" mesmo com a etiqueta do envio ainda como ready_to_ship
        self.assertEqual(dv.fase_da_devolucao(ret("shipped", "ready_to_ship")), "a_caminho")
        self.assertEqual(dv.fase_da_devolucao(ret("label_generated", "ready_to_ship")), "aguardando_postagem")
        self.assertEqual(dv.fase_da_devolucao(ret("cancelled", "cancelled")), "nao_concluida")
        self.assertEqual(dv.fase_da_devolucao(ret("closed")), "encerrada")

    def test_acao_para_o_sac(self):
        self.assertIn("RESPONDER A RECLAMAÇÃO", dv.acao_do_sac("a_caminho", True))
        self.assertNotIn("RESPONDER", dv.acao_do_sac("a_caminho", False))
        self.assertIn("apto ou inapto", dv.acao_do_sac("chegou", False))
        self.assertIn("ficado com o comprador", dv.acao_do_sac("nao_concluida", False))

    def test_item_leva_rastreio_e_fase(self):
        base = {"id": 7, "type": "mediations", "stage": "dispute", "status": "opened", "reason_id": "PDD1", "resource_id": 99,
                "date_created": "2026-11-03T10:00:00Z", "aguarda_voce": True}
        i = dv._item("meli01", base, ret("shipped", "shipped"))
        self.assertEqual((i["conta"], i["claim_id"], i["pedido"], i["fase"]), ("meli01", 7, 99, "a_caminho"))
        self.assertEqual(i["devolucao"]["rastreio"], "MEL1")
        self.assertTrue(i["aguarda_voce"])


class TestInstantaneo(unittest.TestCase):
    def setUp(self):
        dv.CAMINHO = os.path.join(tempfile.mkdtemp(), "c.json")
        dv._estado.update(dados=None, ts=0.0, erro=None, carregando=False)

    def _esperar(self):
        for _ in range(100):
            if not dv._estado["carregando"]:
                return
            time.sleep(0.02)

    def leitor(self, itens):
        return lambda dias: {"success": True, "dias": dias, "itens": itens, "erros": {}, "resumo": dv._resumo(itens)}

    def test_primeira_chamada_nao_espera(self):
        liberar = threading.Event()

        def lento(dias):
            liberar.wait(2)
            return self.leitor([])(dias)

        t0 = time.time()
        s = dv.obter(15, leitor=lento)
        self.assertLess(time.time() - t0, 0.5)
        self.assertIsNone(s["dados"])
        self.assertTrue(s["carregando"])
        liberar.set()
        self._esperar()
        self.assertIsNotNone(dv.obter(15, leitor=lento)["dados"])
        self.assertTrue(os.path.exists(dv.CAMINHO))

    def test_rota_filtra_por_conta(self):
        itens = [{"conta": "meli01", "fase": "a_caminho", "aguarda_voce": False, "data": "1"},
                 {"conta": "meli02", "fase": "chegou", "aguarda_voce": True, "data": "2"}]
        dv.obter(15, leitor=self.leitor(itens)); self._esperar()
        app = FastAPI(); app.include_router(router)
        c = TestClient(app)
        self.assertEqual(len(c.get("/sac/devolucoes").json()["itens"]), 2)
        r = c.get("/sac/meli02/devolucoes").json()
        self.assertEqual([i["conta"] for i in r["itens"]], ["meli02"])
        self.assertEqual(r["resumo"], {"chegou": 1})
        self.assertEqual(c.get("/sac/nada/devolucoes").status_code, 404)
        self.assertEqual(c.get("/sac/devolucoes?dias=999").status_code, 400)


if __name__ == "__main__":
    unittest.main()
