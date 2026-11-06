"""Posição do anúncio: arquivo temporário e planilha simulada, sem rede."""
import json
import os
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import main


class SalvarPosicao(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory(); self.addCleanup(d.cleanup)
        self.arq = os.path.join(d.name, "posicoes.json")
        for alvo, nome, v in ((main, "POSICOES_PATH", self.arq),):
            p = mock.patch.object(alvo, nome, v); p.start(); self.addCleanup(p.stop)
        p = mock.patch.object(main.shared_store, "enviar_async"); self.env = p.start(); self.addCleanup(p.stop)
        self.c = TestClient(main.app)

    def salvar(self, **k):
        return self.c.post("/posicoes/meli02/MLB1", json=k)

    def test_posicao_acima_de_99_salva(self):
        r = self.salvar(pagina=3, posicao=139, keyword="relogio")
        self.assertEqual(r.status_code, 200)
        with open(self.arq, encoding="utf-8") as f:
            salvo = json.load(f)["meli02"]["MLB1"]
        self.assertEqual((salvo["pagina"], salvo["posicao"], salvo["keyword"]), (3, 139, "relogio"))
        self.env.assert_called_once()

    def test_amazon_salva_por_sku_mesmo_com_barra(self):
        r = self.c.post("/posicoes/amazon02/SKU%2FA-1", json={"pagina": 2, "posicao": 120})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get("/posicoes/amazon02").json()["posicoes"]["SKU/A-1"]["posicao"], 120)
        self.assertEqual(self.env.call_args.args[1], "amazon02|SKU/A-1")

    def test_limites_recusam_com_mensagem(self):
        for k in ({"pagina": 1, "posicao": 0}, {"pagina": 1, "posicao": 1000000}, {"pagina": 100000, "posicao": 1}, {"pagina": 1, "posicao": -1}):
            r = self.salvar(**k)
            self.assertEqual(r.status_code, 400, k)
            self.assertIn("posição entre 1 e 999999", r.json()["detail"])
        self.assertFalse(os.path.exists(self.arq))
        self.env.assert_not_called()

    def test_historico_guarda_as_anteriores(self):
        self.salvar(pagina=1, posicao=10)
        self.salvar(pagina=2, posicao=150)
        with open(self.arq, encoding="utf-8") as f:
            salvo = json.load(f)["meli02"]["MLB1"]
        self.assertEqual([h["posicao"] for h in salvo["historico"]], [10, 150])


if __name__ == "__main__":
    unittest.main()
