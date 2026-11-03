import time
import unittest
from unittest import mock

from marketplaces.bling import api, estoque


class TestSaldoDesatualizado(unittest.TestCase):
    def setUp(self):
        estoque._cache.clear()
        estoque._falha.clear()
        api._ESTADO.clear()
        self._p = mock.patch.object(estoque, "_salvar_disco")
        self._p.start()

    def tearDown(self):
        self._p.stop()
        estoque._cache.clear()
        estoque._falha.clear()
        api._ESTADO.clear()

    def test_falha_com_cache_devolve_ultimo_saldo_e_marca_desatualizado(self):
        dados = {"saldos": {"A": 5}, "pais": {}, "virtuais": {"A": 4}}
        estoque._cache["bling01"] = (time.time() - 3600, dados)
        with mock.patch.object(estoque, "_carregar", side_effect=RuntimeError("401")):
            self.assertEqual(estoque.saldos("bling01", forcar=True), dados)
        self.assertIn("bling01", estoque._falha)
        self.assertTrue(estoque.estado()["bling01"]["desatualizado"])

    def test_falha_sem_cache_levanta(self):
        with mock.patch.object(estoque, "_carregar", side_effect=RuntimeError("401")):
            with self.assertRaises(RuntimeError):
                estoque.saldos("bling02", forcar=True)

    def test_sucesso_limpa_a_falha(self):
        estoque._falha["bling01"] = {"erro": "x", "desde": "2026-11-02T10:00:00"}
        estoque._cache["bling01"] = (time.time(), {"saldos": {}, "pais": {}, "virtuais": {}})
        with mock.patch.object(estoque, "_carregar", return_value={"saldos": {"A": 1}, "pais": {}, "virtuais": {}}):
            estoque.saldos("bling01", forcar=True)
        self.assertNotIn("bling01", estoque._falha)

    def test_por_conta_informa_os_desatualizados(self):
        estoque._cache["bling01"] = (time.time(), {"saldos": {"A": 1}, "pais": {}, "virtuais": {}})
        estoque._falha["bling01"] = {"erro": "x", "desde": "2026-11-02T10:00:00"}
        with mock.patch.object(estoque, "saldos", side_effect=lambda b, f=False: estoque._cache[b][1] if b in estoque._cache else (_ for _ in ()).throw(RuntimeError("sem"))):
            r = estoque.por_conta("meli01")
        self.assertIn("bling01", r["desatualizados"])


class TestTokenMorto(unittest.TestCase):
    def setUp(self):
        api._ESTADO.clear()

    def tearDown(self):
        api._ESTADO.clear()

    def test_invalid_grant_marca_para_reautorizar_e_nao_insiste(self):
        cfg = {"contas": {"bling02": {"access_token": "a", "refresh_token": "r1", "client_id": "i", "client_secret": "s"}}}
        resp = mock.Mock(status_code=400, text='{"error":"invalid_grant"}')
        with mock.patch.object(api, "load_config", return_value=cfg), mock.patch.object(api.httpx, "post", return_value=resp) as post:
            r1 = api.refresh_token("bling02")
            r2 = api.refresh_token("bling02")   # mesma refresh_token, dentro da espera: não chama o Bling de novo
        self.assertFalse(r1["success"])
        self.assertFalse(r2["success"])
        self.assertEqual(post.call_count, 1)
        self.assertIn("bling02", api.estado_contas())
        self.assertNotIn("refresh_falho", api.estado_contas()["bling02"])

    def test_refresh_token_novo_tenta_de_novo(self):
        api._marcar_morta("bling02", "400", "r1")
        cfg = {"contas": {"bling02": {"access_token": "a", "refresh_token": "r2", "client_id": "i", "client_secret": "s"}}}
        ok = mock.Mock(status_code=200, text="{}")
        ok.json.return_value = {"access_token": "n", "refresh_token": "r3", "expires_in": 21600}
        with mock.patch.object(api, "load_config", return_value=cfg), mock.patch.object(api, "save_config"), \
                mock.patch.object(api.httpx, "post", return_value=ok):
            self.assertTrue(api.refresh_token("bling02")["success"])
        self.assertNotIn("bling02", api.estado_contas())


if __name__ == "__main__":
    unittest.main()
