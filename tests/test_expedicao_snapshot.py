"""Instantâneo do painel da Expedição — sem rede."""
import os
import tempfile
import threading
import time
import unittest

from marketplaces.expedicao import snapshot


class TestSnapshot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        snapshot.CAMINHO = os.path.join(self.dir, "snap.json")
        snapshot._estado.update(dados=None, ts=0.0, erro=None, carregando=False)

    def _esperar(self):
        for _ in range(100):
            if not snapshot._estado["carregando"]:
                return
            time.sleep(0.02)

    def test_primeira_chamada_nao_espera_e_a_seguinte_traz_dados(self):
        liberar = threading.Event()

        def leitor():
            liberar.wait(2)
            return {"linhas": [{"envio": "1"}], "erros": {}}

        t0 = time.time()
        s = snapshot.obter(leitor=leitor)
        self.assertLess(time.time() - t0, 0.5)          # não esperou a leitura lenta
        self.assertIsNone(s["dados"])
        self.assertTrue(s["carregando"])
        liberar.set()
        self._esperar()
        s = snapshot.obter(leitor=leitor)
        self.assertEqual(s["dados"]["linhas"], [{"envio": "1"}])
        self.assertIsNotNone(s["iso"])
        self.assertTrue(os.path.exists(snapshot.CAMINHO))   # gravado em disco para a próxima subida

    def test_dentro_do_prazo_nao_le_de_novo(self):
        chamadas = []

        def leitor():
            chamadas.append(1)
            return {"linhas": [], "erros": {}}

        snapshot.obter(leitor=leitor); self._esperar()
        snapshot.obter(leitor=leitor); self._esperar()
        self.assertEqual(len(chamadas), 1)

    def test_falha_total_mantem_o_ultimo_bom(self):
        snapshot.obter(leitor=lambda: {"linhas": [{"envio": "9"}], "erros": {}}); self._esperar()
        snapshot._estado["ts"] = 0.0                       # força vencimento
        snapshot.obter(leitor=lambda: {"linhas": [], "erros": {"meli01": "HTTP 500"}}); self._esperar()
        s = snapshot.obter(leitor=lambda: {"linhas": [{"envio": "9"}], "erros": {}})
        self.assertEqual(s["dados"]["linhas"], [{"envio": "9"}])

    def test_reaproveita_o_disco_apos_reiniciar(self):
        snapshot.obter(leitor=lambda: {"linhas": [{"envio": "7"}], "erros": {}}); self._esperar()
        snapshot._estado.update(dados=None, ts=0.0)         # simula servidor reiniciado
        s = snapshot.obter(leitor=lambda: {"linhas": [], "erros": {}})
        self.assertEqual(s["dados"]["linhas"], [{"envio": "7"}])


class TestShopeeDesligada(unittest.TestCase):
    def rodar(self, liberada):
        from unittest import mock
        from marketplaces.expedicao import snapshot as sn
        with mock.patch.object(sn.ml, "mapa", lambda *a, **k: {"linhas": [], "erros": {}}), mock.patch.object(sn.bling_checkout, "enriquecer", lambda l: (l, {})), \
                mock.patch.object(sn.amazon_dba, "linhas", lambda: ([], {})), mock.patch.object(sn, "_shopee_liberada", lambda: liberada), \
                mock.patch.object(sn.shopee_bling, "linhas", return_value=([{"conta": "shopee"}], {})) as sh:
            return sn.montar(), sh

    def test_fechada_nao_le_o_bling_nem_conta_pedido(self):
        r, sh = self.rodar(False)
        sh.assert_not_called()
        self.assertEqual(r["linhas"], [])

    def test_aberta_entra_no_painel(self):
        r, sh = self.rodar(True)
        sh.assert_called_once()
        self.assertEqual(r["linhas"], [{"conta": "shopee"}])

    def test_sai_fechada_por_padrao(self):
        import modulos_liberados
        self.assertFalse(modulos_liberados.LIBERADOS["shopee_expedicao"])


if __name__ == "__main__":
    unittest.main()
