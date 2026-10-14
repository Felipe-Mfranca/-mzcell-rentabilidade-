"""Testes de promocoes_ml.py — sem rede, sem credencial, sem tocar no ML."""
import os, sys, tempfile, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import promocoes_ml as pm

LIGHTNING = {"id": "LGH-MLB1000", "type": "LIGHTNING", "status": "candidate", "price": 34.99,
             "stock": {"min": 5, "max": 396}}
SMART = {"id": "P-MLB1", "type": "SMART", "status": "candidate", "price": 31.47,
         "ref_id": "CANDIDATE-MLB1-9"}
DEAL = {"id": "P-MLB2", "type": "DEAL", "status": "candidate", "price": 0}


class Corpo(unittest.TestCase):
    def test_lightning_usa_disponivel_limitado_ao_max(self):
        self.assertEqual(pm.montar_corpo(LIGHTNING, 383)["stock"], 383)
        self.assertEqual(pm.montar_corpo(LIGHTNING, 900)["stock"], 396)

    def test_lightning_corpo_completo(self):
        self.assertEqual(pm.montar_corpo(LIGHTNING, 383, 100), {
            "promotion_id": "LGH-MLB1000", "promotion_type": "LIGHTNING",
            "deal_price": 34.99, "stock": 100})

    def test_lightning_estoque_invalido(self):
        for est in (4, 397, 0):
            with self.assertRaises(pm.ErroPromocao):
                pm.montar_corpo(LIGHTNING, 900, est)
        with self.assertRaises(pm.ErroPromocao):
            pm.montar_corpo(LIGHTNING, 50, 100)  # acima do disponível

    def test_smart_usa_ref_id_como_offer_id(self):
        self.assertEqual(pm.montar_corpo(SMART, 10), {
            "promotion_id": "P-MLB1", "promotion_type": "SMART", "offer_id": "CANDIDATE-MLB1-9"})

    def test_deal_so_ids(self):
        self.assertEqual(pm.montar_corpo(DEAL, 10), {"promotion_id": "P-MLB2", "promotion_type": "DEAL"})

    def test_tipo_nao_suportado(self):
        with self.assertRaises(pm.ErroPromocao):
            pm.montar_corpo({"id": "x", "type": "PRICE_DISCOUNT", "status": "candidate"}, 10)


class Localizar(unittest.TestCase):
    def test_so_aceita_candidata(self):
        self.assertIs(pm.localizar_candidata([SMART], "P-MLB1"), SMART)
        for status in ("pending", "started"):
            with self.assertRaises(pm.ErroPromocao):
                pm.localizar_candidata([{**SMART, "status": status}], "P-MLB1")
        with self.assertRaises(pm.ErroPromocao):
            pm.localizar_candidata([SMART], "outra")


class FakeML:
    """Simula as duas leituras que entrar() faz; registra se algo foi enviado."""
    def __init__(self, conta):
        pass

    def get(self, path, **params):
        if path.startswith("/seller-promotions"):
            return 200, [LIGHTNING]
        return 200, {"id": "MLB1", "title": "Torneira", "available_quantity": 383, "status": "active"}


class Entrar(unittest.TestCase):
    def setUp(self):
        self.log = os.path.join(tempfile.mkdtemp(), "log.jsonl")
        for p in (mock.patch.object(pm, "ML", FakeML), mock.patch.object(pm, "LOG", self.log),
                  mock.patch.object(pm.ml_api, "_headers", lambda c: {})):
            p.start(); self.addCleanup(p.stop)

    def test_simulacao_nao_envia_nem_registra(self):
        with mock.patch.object(pm.httpx, "post") as post:
            r = pm.entrar("meli03", "MLB1", "LGH-MLB1000")
        post.assert_not_called()
        self.assertFalse(r["enviado"])
        self.assertEqual(r["corpo"]["stock"], 383)
        self.assertFalse(os.path.exists(self.log))

    def test_confirmar_envia_uma_vez_e_registra(self):
        resp = mock.Mock(status_code=201)
        resp.json.return_value = {"offer_id": "OFFER-1"}
        with mock.patch.object(pm.httpx, "post", return_value=resp) as post:
            r = pm.entrar("meli03", "MLB1", "LGH-MLB1000", confirmar=True, usuario="teste")
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["json"]["deal_price"], 34.99)
        self.assertTrue(r["ok"])
        linha = open(self.log, encoding="utf-8").read()
        self.assertIn("OFFER-1", linha)
        self.assertIn('"usuario": "teste"', linha)

    def test_recusa_do_ml_aparece_e_e_registrada(self):
        resp = mock.Mock(status_code=400)
        resp.json.return_value = {"message": "invalid"}
        with mock.patch.object(pm.httpx, "post", return_value=resp):
            r = pm.entrar("meli03", "MLB1", "LGH-MLB1000", confirmar=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["http"], 400)
        self.assertTrue(os.path.exists(self.log))

    def test_candidata_inexistente_nao_chama_o_ml(self):
        with mock.patch.object(pm.httpx, "post") as post:
            with self.assertRaises(pm.ErroPromocao):
                pm.entrar("meli03", "MLB1", "NAO-EXISTE", confirmar=True)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
