"""Trava de acesso pela rede interna — sem rede."""
import unittest

from marketplaces.acesso_rede import caminho_liberado


class TestAcessoRede(unittest.TestCase):
    def test_liberados(self):
        for p in ("/expedicao/tv", "/expedicao/painel", "/expedicao/dia", "/expedicao/bipar", "/expedicao/bipar/buscar", "/montagem/tv", "/montagem/tv/dados",
                  "/montagem/tv-full", "/tv/expedicao", "/static/x.js", "/sistema/versao"):
            self.assertTrue(caminho_liberado(p), p)

    def test_bloqueados(self):
        for p in ("/", "/config", "/usuarios", "/amazon/contas", "/sac/devolucoes", "/checkout/x", "/financeiro/estado",
                  "/bling/token", "/sistema/abrir-link", "/expedicaoX", "/montagem/../config", "/docs", "/openapi.json",
                  "/produtos/custos", "/expedicao", "/expedicao/sem-checkout", "/expedicao/biparx", "/expedicao/marca",
                  "/expedicao/galpao", "/expedicao/cancelamentos", "/expedicao/full", "/montagem", "/montagem/dados",
                  "/montagem/envios", "/montagem/produtividade", "/montagem/pdf", "/montagem/tvx", "/fotos/meli01",
                  "/bling/estoque", "/bling/estoque/ABC"):
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
        r = c.get("/galpao")                                                           # menu das telas do galpão
        self.assertEqual(r.status_code, 200)
        self.assertIn("/expedicao/tv", r.text)

    def test_login_pela_rede(self):
        from fastapi import FastAPI, Response
        from fastapi.testclient import TestClient
        from marketplaces import acesso_rede
        app = FastAPI()

        @app.get("/")
        def _raiz():
            return {"tela": "login"}

        @app.post("/auth/login")
        def _login(response: Response):
            response.set_cookie(acesso_rede.COOKIE, acesso_rede.criar_sessao("felipe"))
            return {"success": True}

        for rota in ("/produtos/meli01", "/config/contas", "/auth/url/meli01", "/sistema/abrir-link"):
            app.get(rota)(lambda: {"ok": 1})
        acesso_rede.instalar(app)

        c = TestClient(app, client=("192.168.15.50", 5000))
        self.assertEqual(c.get("/").status_code, 200)                                  # tela de login abre
        self.assertEqual(c.get("/produtos/meli01").status_code, 401)                   # sem login, não
        c.post("/auth/login")
        self.assertEqual(c.get("/produtos/meli01").status_code, 200)                   # com login, sim
        for rota in ("/config/contas", "/auth/url/meli01", "/sistema/abrir-link"):     # credenciais: nunca pela rede
            self.assertEqual(c.get(rota).status_code, 403, rota)
        c.cookies.set(acesso_rede.COOKIE, "inventado")
        self.assertEqual(c.get("/produtos/meli01").status_code, 401)                   # cookie falso não passa

    def test_gestao_pede_login_e_tv_nao(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from marketplaces import acesso_rede
        app = FastAPI()
        rotas = ("/expedicao", "/expedicao/sem-checkout", "/expedicao/bipar", "/expedicao/dia", "/expedicao/painel", "/montagem", "/montagem/dados",
                 "/montagem/tv/dados")
        for rota in rotas:
            app.get(rota)(lambda: {"ok": 1})
        app.post("/montagem/envios")(lambda: {"ok": 1})
        acesso_rede.instalar(app)
        html = {"accept": "text/html,*/*"}

        c = TestClient(app, client=("192.168.15.50", 5000), follow_redirects=False)
        for rota in ("/expedicao/painel", "/expedicao/dia", "/expedicao/bipar", "/montagem/tv/dados"):     # TV e bipar abrem sem login
            self.assertEqual(c.get(rota, headers=html).status_code, 200, rota)
        r = c.get("/expedicao?x=1", headers=html)                                      # tela da gestão: vai ao login e volta
        self.assertEqual(r.status_code, 303)
        self.assertEqual(r.headers["location"], "/?volta=%2Fexpedicao%3Fx%3D1")
        self.assertEqual(c.get("/expedicao/sem-checkout").status_code, 401)           # lista pedido a pedido: só com login
        self.assertEqual(c.get("/montagem/dados").status_code, 401)                    # dados sem login: 401, sem redirecionar
        self.assertEqual(c.post("/montagem/envios", headers=html).status_code, 401)    # gravação nunca passa

        c.cookies.set(acesso_rede.COOKIE, acesso_rede.criar_sessao("felipe"))
        for rota in rotas:
            self.assertEqual(c.get(rota, headers=html).status_code, 200, rota)         # com login, tudo abre
        self.assertEqual(c.post("/montagem/envios").status_code, 200)

        local = TestClient(app, follow_redirects=False)                                # app no próprio PC segue sem login
        self.assertEqual(local.get("/montagem/dados").status_code, 200)

    def test_sessao_vence(self):
        from marketplaces import acesso_rede
        t = acesso_rede.criar_sessao("x")
        self.assertTrue(acesso_rede.sessao_valida(t))
        acesso_rede._sessoes[t] = ("x", 0)
        self.assertFalse(acesso_rede.sessao_valida(t))
        self.assertFalse(acesso_rede.sessao_valida(None))


if __name__ == "__main__":
    unittest.main()
