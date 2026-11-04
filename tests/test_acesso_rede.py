"""Trava de acesso pela rede interna — sem rede."""
import unittest

from marketplaces.acesso_rede import caminho_liberado


class TestAcessoRede(unittest.TestCase):
    def test_liberados(self):
        for p in ("/expedicao/painel", "/expedicao", "/montagem/envios", "/tv/expedicao", "/static/x.js",
                  "/fotos/meli01", "/sistema/versao", "/bling/estoque", "/bling/estoque/ABC"):
            self.assertTrue(caminho_liberado(p), p)

    def test_bloqueados(self):
        for p in ("/", "/config", "/usuarios", "/amazon/contas", "/sac/devolucoes", "/checkout/x", "/financeiro/estado",
                  "/bling/token", "/sistema/abrir-link", "/expedicaoX", "/montagem/../config", "/docs", "/openapi.json",
                  "/produtos/custos"):
            self.assertFalse(caminho_liberado(p), p)

    def test_middleware(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from marketplaces import acesso_rede
        app = FastAPI()

        @app.get("/config")
        def _c():
            return {"x": 1}
        acesso_rede.instalar(app)
        self.assertEqual(TestClient(app).get("/config").status_code, 200)             # local
        c = TestClient(app, client=("192.168.15.50", 5000))
        self.assertEqual(c.get("/config").status_code, 403)                            # de outro PC
        r = c.get("/")                                                                 # a raiz mostra o menu, não o app
        self.assertEqual(r.status_code, 200)
        self.assertIn("/expedicao/tv", r.text)


if __name__ == "__main__":
    unittest.main()
