"""Testes do módulo Shopee (fase 1): sem rede e sem credencial real."""
import hashlib
import hmac
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from marketplaces.shopee import api, lojas
from marketplaces.shopee.lojas import ErroShopee

ENV = {"SHOPEE_ENV": "sandbox", "SHOPEE_SANDBOX_BASE_URL": "https://exemplo.invalid",
       "SHOPEE_SANDBOX_PARTNER_ID": "1001", "SHOPEE_SANDBOX_PARTNER_KEY": "chave-secreta-de-teste",
       "SHOPEE_REDIRECT_URL": "http://127.0.0.1:8001/shopee/retorno"}


class Resp:
    def __init__(self, dados, status=200):
        self._d, self.status_code = dados, status

    def json(self):
        return self._d


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(api, "TOKENS_PATH", os.path.join(self.dir.name, "tokens.json"))
        p.start(); self.addCleanup(p.stop)
        s = mock.patch("time.sleep"); s.start(); self.addCleanup(s.stop)

    def token(self, shop=7, expira_em=3600, salvo_ha_dias=0):
        agora = int(time.time())
        with open(api.TOKENS_PATH, "w", encoding="utf-8") as f:
            json.dump({"SANDBOX": {str(shop): {"access_token": "AT-segredo-123", "refresh_token": "RT-segredo-456",
                                                 "expires_at": agora + expira_em,
                                                 "saved_at": agora - salvo_ha_dias * 86400}}}, f)


class TestAssinatura(Base):
    def test_hmac_publico_e_de_loja(self):
        esperado = hmac.new(b"chave-secreta-de-teste", b"1001/api/v2/x1700000000", hashlib.sha256).hexdigest()
        self.assertEqual(api.assinar("/api/v2/x", 1700000000, env=ENV), esperado)
        esperado = hmac.new(b"chave-secreta-de-teste", b"1001/api/v2/x1700000000TOK7", hashlib.sha256).hexdigest()
        self.assertEqual(api.assinar("/api/v2/x", 1700000000, "TOK", 7, env=ENV), esperado)

    def test_url_de_autorizacao_nao_tem_a_chave(self):
        url = api.url_autorizacao(ENV)
        self.assertIn("partner_id=1001", url)
        self.assertNotIn("chave-secreta-de-teste", url)

    def test_sem_chave_no_env_recusa(self):
        with self.assertRaises(ErroShopee):
            api.assinar("/x", 1, env={"SHOPEE_ENV": "sandbox"})


class TestMascara(unittest.TestCase):
    def test_remove_parametros_e_segredos(self):
        t = api.mascarar("GET ?access_token=ABC123&sign=zzz999&shop_id=7 falhou: segredo-longo-1", ["segredo-longo-1"])
        self.assertNotIn("ABC123", t); self.assertNotIn("zzz999", t); self.assertNotIn("segredo-longo-1", t)
        self.assertIn("shop_id=7", t)


class TestToken(Base):
    def test_trocar_codigo_grava_token_e_nao_devolve_segredo(self):
        resp = {"access_token": "A1", "refresh_token": "R1", "expire_in": 14400}
        with mock.patch("httpx.post", return_value=Resp(resp)):
            r = api.trocar_codigo("cod-secreto-1", 7, ENV)
        self.assertEqual(r, {"shop_id": 7, "expire_in": 14400})
        self.assertEqual(api._ler_tokens()["SANDBOX"]["7"]["refresh_token"], "R1")

    def test_erro_do_code_nao_vaza_o_code(self):
        resp = {"error": "error_auth", "message": "invalid cod-secreto-1"}
        with mock.patch("httpx.post", return_value=Resp(resp)):
            with self.assertRaises(ErroShopee) as c:
                api.trocar_codigo("cod-secreto-1", 7, ENV)
        self.assertNotIn("cod-secreto-1", str(c.exception))

    def test_token_vencido_renova_antes_da_chamada(self):
        self.token(expira_em=-10)
        novo = {"access_token": "A2", "refresh_token": "R2", "expire_in": 14400}
        with mock.patch("httpx.post", return_value=Resp(novo)) as p, \
             mock.patch("httpx.get", return_value=Resp({"response": {}})) as g:
            api.chamar("/api/v2/shop/get_shop_info", 7, env=ENV)
        self.assertEqual(p.call_count, 1)
        self.assertEqual(g.call_args.kwargs["params"]["access_token"], "A2")
        self.assertEqual(api._ler_tokens()["SANDBOX"]["7"]["refresh_token"], "R2")

    def test_token_recusado_renova_uma_vez(self):
        self.token()
        novo = {"access_token": "A3", "refresh_token": "R3", "expire_in": 14400}
        respostas = [Resp({"error": "error_auth", "message": "Invalid access_token"}), Resp({"response": {"ok": 1}})]
        with mock.patch("httpx.post", return_value=Resp(novo)), mock.patch("httpx.get", side_effect=respostas):
            self.assertEqual(api.chamar("/api/v2/x", 7, env=ENV)["response"], {"ok": 1})

    def test_rate_limit_recua_e_depois_desiste(self):
        self.token()
        with mock.patch("httpx.get", return_value=Resp({"error": "error_rate_limit", "message": "x"}, 429)) as g:
            with self.assertRaises(ErroShopee) as c:
                api.chamar("/api/v2/x", 7, env=ENV)
        self.assertEqual(c.exception.status, 429)
        self.assertEqual(g.call_count, 4)

    def test_erro_de_rede_nao_vaza_token(self):
        self.token()
        import httpx
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("falha ?access_token=AT-segredo-123&sign=abc")):
            with self.assertRaises(ErroShopee) as c:
                api.chamar("/api/v2/x", 7, env=ENV)
        self.assertNotIn("AT-segredo-123", str(c.exception))

    def test_loja_sem_token(self):
        with self.assertRaises(ErroShopee) as c:
            api.chamar("/api/v2/x", 99, env=ENV)
        self.assertEqual(c.exception.status, 401)


class TestJanelas(unittest.TestCase):
    def test_45_dias_viram_3_janelas_de_15(self):
        from datetime import datetime
        j = list(api.janelas(datetime(2026, 1, 1), datetime(2026, 2, 15)))
        self.assertEqual(len(j), 3)
        self.assertTrue(all(b - a <= 15 * 86400 for a, b in j))
        self.assertEqual(j[0][1], j[1][0])


class TestAlerta(Base):
    def nivel(self, dias):
        self.token(salvo_ha_dias=dias)
        return api.estado_autorizacao(7, ENV)

    def test_niveis(self):
        self.assertEqual(self.nivel(5)["nivel"], "ok")
        self.assertEqual(self.nivel(24)["nivel"], "amarelo")     # faltam 6 dias
        self.assertEqual(self.nivel(28)["nivel"], "vermelho")    # faltam 2 dias
        e = self.nivel(31)
        self.assertEqual((e["nivel"], e["autorizada"]), ("vermelho", False))

    def test_sem_token_e_vermelho_e_nao_expoe_token(self):
        e = api.estado_autorizacao(99, ENV)
        self.assertFalse(e["autorizada"])
        self.token()
        self.assertNotIn("segredo", json.dumps(api.estado_autorizacao(7, ENV)))


class TestLojas(unittest.TestCase):
    def test_arquivo_de_lojas_e_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "lojas.json")
            self.assertEqual(lojas.lojas(ENV, p), {})
            self.assertEqual(lojas.lojas({**ENV, "SHOPEE_SANDBOX_SHOP_ID": "55"}, p)["padrao"]["shop_id"], 55)
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"techseller": {"nome": "Techseller", "shop_id": "11"}, "mizi": {"nome": "Mizi", "shop_id": 22}}, f)
            self.assertEqual(lojas.shop_id_da("techseller", ENV, p), 11)
            with self.assertRaises(ErroShopee):
                lojas.shop_id_da("outra", ENV, p)

    def test_ambiente_padrao_e_sandbox_e_invalido_recusa(self):
        self.assertEqual(lojas.ambiente({}), "SANDBOX")
        with self.assertRaises(ErroShopee):
            lojas.ambiente({"SHOPEE_ENV": "producao"})

    def test_ler_env_comentarios_e_aspas(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# c\nSHOPEE_ENV=prod\nX=\"a b\"\n\nruim\n")
            e = lojas.ler_env(p)
            self.assertEqual((e["SHOPEE_ENV"], e["X"]), ("prod", "a b"))


class TestIntegracaoComOApp(Base):
    def test_contrato_marketplace(self):
        from marketplaces.base import Marketplace
        from marketplaces.shopee.marketplace import ShopeeMarketplace
        m = ShopeeMarketplace()
        self.assertIsInstance(m, Marketplace)
        self.assertEqual(m.nome, "shopee")
        with self.assertRaises(NotImplementedError):
            m.listar_pedidos("x", "2026-01-01", "2026-01-02")

    def test_retorno_troca_codigo_so_de_loja_cadastrada(self):
        from fastapi.testclient import TestClient
        import main
        c = TestClient(main.app)
        arq = os.path.join(self.dir.name, "lojas.json")
        with open(arq, "w", encoding="utf-8") as f:
            json.dump({"mizi": {"nome": "Mizi", "shop_id": 22}}, f)
        resp = {"access_token": "A", "refresh_token": "R", "expire_in": 14400}
        with mock.patch.object(lojas, "LOJAS_PATH", arq), mock.patch.object(lojas, "ler_env", return_value=ENV),              mock.patch("httpx.post", return_value=Resp(resp)):
            self.assertEqual(c.get("/shopee/retorno?code=abc&shop_id=999").status_code, 400)
            r = c.get("/shopee/retorno?code=abc&shop_id=22")
            self.assertEqual(r.status_code, 200)
            self.assertIn("autorizada", r.text)
            self.assertTrue(api.estado_autorizacao(22, ENV)["autorizada"])


if __name__ == "__main__":
    unittest.main()
