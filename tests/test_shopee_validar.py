"""Validador do ambiente Shopee: sem rede e sem credencial real."""
import unittest
from unittest import mock

from marketplaces.shopee import api, validar

ENV = {"SHOPEE_ENV": "sandbox", "SHOPEE_SANDBOX_BASE_URL": "https://exemplo.invalid",
       "SHOPEE_SANDBOX_PARTNER_ID": "1001", "SHOPEE_SANDBOX_PARTNER_KEY": "a" * 64,
       "SHOPEE_REDIRECT_URL": "http://127.0.0.1:8001/shopee/retorno"}


class Resp:
    def __init__(self, status=200, dados=None, tipo="text/html"):
        self.status_code, self._d, self.headers = status, dados, {"content-type": tipo}

    def json(self):
        return self._d


class Formato(unittest.TestCase):
    def test_env_bom(self):
        self.assertEqual([x for x in validar.checar_formato(ENV) if x[0] == "erro"], [])

    def test_campos_faltando(self):
        r = validar.checar_formato({"SHOPEE_ENV": "sandbox"})
        self.assertEqual(len([x for x in r if x[0] == "erro"]), 3)

    def test_http_e_espaco_sao_erros(self):
        r = validar.checar_formato({**ENV, "SHOPEE_SANDBOX_BASE_URL": "http://x", "SHOPEE_SANDBOX_PARTNER_KEY": "ab cd"})
        self.assertEqual(len([x for x in r if x[0] == "erro"]), 2)

    def test_ambiente_invalido(self):
        self.assertEqual(validar.checar_formato({"SHOPEE_ENV": "xx"})[0][0], "erro")

    def test_prod_avisa(self):
        env = {k.replace("SANDBOX", "PROD"): v for k, v in ENV.items()}
        env["SHOPEE_ENV"] = "prod"
        self.assertTrue(any("PROD" in m for _, m in validar.checar_formato(env)))


class Assinatura(unittest.TestCase):
    def test_pagina_ok(self):
        with mock.patch("httpx.get", return_value=Resp(200)):
            self.assertTrue(validar.testar_assinatura(ENV)[0])

    def test_assinatura_errada_nao_vaza_a_chave(self):
        r = Resp(200, {"error": "error_sign", "message": "Wrong sign"}, "application/json")
        with mock.patch("httpx.get", return_value=r):
            ok, det = validar.testar_assinatura(ENV)
        self.assertFalse(ok)
        self.assertIn("mesmo ambiente", det)
        self.assertNotIn("a" * 64, det)

    def test_erro_de_rede(self):
        import httpx
        with mock.patch("httpx.get", side_effect=httpx.ConnectError("falhou")):
            self.assertFalse(validar.testar_assinatura(ENV)[0])


if __name__ == "__main__":
    unittest.main()
