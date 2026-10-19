"""Validador de credenciais Amazon: formato e fluxo, sem rede."""
import unittest
from unittest import mock

from marketplaces.amazon import api, validar

BOM = {"refresh_token": "Atzr|" + "x" * 300,
       "client_id": "amzn1.application-oa2-client." + "a" * 32,
       "client_secret": "amzn1.oa2-cs.v1." + "b" * 64}


class Formato(unittest.TestCase):
    def test_credencial_boa_sem_avisos(self):
        self.assertEqual(validar.checar_formato(BOM), [])

    def test_placeholder_e_vazio(self):
        r = validar.checar_formato({"refresh_token": "COLE_AQUI", "client_id": "", "client_secret": None})
        self.assertEqual(len([n for n, _ in r if n == "erro"]), 3)

    def test_espaco_e_aspas(self):
        r = validar.checar_formato({**BOM, "client_id": ' "' + BOM["client_id"] + '"'})
        self.assertTrue(any(n == "erro" and "espaço" in m for n, m in r))

    def test_trocados(self):
        r = validar.checar_formato({**BOM, "client_id": BOM["client_secret"], "client_secret": BOM["client_id"]})
        self.assertTrue(any("trocados" in m for _, m in r))

    def test_formato_estranho_e_so_aviso(self):
        r = validar.checar_formato({**BOM, "refresh_token": "abc" * 50})
        self.assertTrue(r and all(n == "aviso" for n, _ in r))


class Fluxo(unittest.TestCase):
    def test_sucesso(self):
        corpo = {"payload": [{"marketplace": {"id": api.MARKETPLACE_BR}}]}
        with mock.patch.object(api, "conta_cfg", return_value=BOM), mock.patch.object(api, "access_token", return_value="t"), \
             mock.patch.object(api, "get", return_value=(200, corpo)):
            r = validar.validar("amazon01")
        self.assertTrue(r["ok"])

    def test_lwa_recusa_para_na_etapa_e_nao_vaza(self):
        with mock.patch.object(api, "conta_cfg", return_value=BOM), \
             mock.patch.object(api, "access_token", side_effect=api.ErroAmazon("LWA recusou a credencial (401): invalid_client", 401)):
            r = validar.validar("amazon01")
        self.assertFalse(r["ok"])
        self.assertEqual(r["etapas"][-1][0], "Token LWA aceito pela Amazon")
        self.assertNotIn(BOM["client_secret"], str(r))

    def test_sem_marketplace_brasil(self):
        corpo = {"payload": [{"marketplace": {"id": "ATVPDKIKX0DER"}}]}
        with mock.patch.object(api, "conta_cfg", return_value=BOM), mock.patch.object(api, "access_token", return_value="t"), \
             mock.patch.object(api, "get", return_value=(200, corpo)):
            self.assertFalse(validar.validar("amazon01")["ok"])


if __name__ == "__main__":
    unittest.main()
