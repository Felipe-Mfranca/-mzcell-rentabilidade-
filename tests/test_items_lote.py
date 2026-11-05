"""Consulta em lote /items?ids= recusada pelo ML (403 "Resource deprecated") cai para um a um — sem rede."""
import unittest
from unittest import mock

import reposicao_full


class _Resp:
    def __init__(self, status, corpo):
        self.status_code, self._corpo = status, corpo
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._corpo


class _Cli:
    def __init__(self):
        self.chamadas = []

    def get(self, url, params=None, headers=None):
        path = url.replace(reposicao_full.B, "")
        self.chamadas.append((path, dict(params or {})))
        if path == "/items":
            return _Resp(403, {"message": "Resource deprecated - client no longer allowed to consume this resource"})
        mlb = path.rsplit("/", 1)[1]
        if mlb == "MLB3":
            return _Resp(404, {"message": "not found"})
        return _Resp(200, {"id": mlb, "title": "t" + mlb})


class TestItemsLote(unittest.TestCase):
    def test_lote_recusado_vira_um_a_um(self):
        with mock.patch.object(reposicao_full.ml_api, "get_token", return_value="tok"):
            ml = reposicao_full.ML("meli01")
            ml.cli = _Cli()
            st, lote = ml.get("/items", ids="MLB1,MLB2,MLB3", attributes="id,title")
        self.assertEqual(st, 200)
        self.assertEqual([e["code"] for e in lote], [200, 200, 404])
        self.assertEqual(lote[0]["body"]["id"], "MLB1")
        self.assertEqual(ml.cli.chamadas[1], ("/items/MLB1", {"attributes": "id,title"}))   # mantém os campos pedidos

    def test_outro_403_nao_muda(self):
        with mock.patch.object(reposicao_full.ml_api, "get_token", return_value="tok"):
            ml = reposicao_full.ML("meli01")
            ml.cli = _Cli()
            ml.cli.get = lambda url, params=None, headers=None: _Resp(403, {"message": "forbidden"})
            st, corpo = ml.get("/orders/search", seller="1")
        self.assertEqual(st, 403)


if __name__ == "__main__":
    unittest.main()
